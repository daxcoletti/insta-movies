#!/usr/bin/env python3
"""
identify_songs.py — identifica la canción (título + artista) de la que habla un post
de @owencutts, en capas, parando apenas una funciona:

  1. CAPTION   — Claude lee el texto del post.
  2. VISION    — Claude mira el thumbnail del Reel (texto sobreimpreso).
  3. AUDIO     — se transcribe el audio (Whisper) y Claude lo lee.

Devuelve (cancion, artista, metodo). Si ninguna capa identifica algo, devuelve
("", "", "no identificado"). Lo usan build_music_spreadsheet.py y daily_music_update.py.

La Claude API no procesa audio: la capa 3 transcribe primero (transcribe.py) y recién
ahí Claude lee texto. El modelo es claude-sonnet-4-6, igual que extract_movies.py.
"""

import base64
import json
import re
import sys

import requests

from ig_feed import caption_text, thumbnail_url, video_url
from transcribe import transcribe_url

MODEL = "claude-sonnet-4-6"
NONE_SENTINEL = "NONE"

SYSTEM_PROMPT = (
    "Sos un asistente que identifica la CANCIÓN de la que habla un post de Instagram "
    "del perfil @owencutts, que comenta y analiza temas musicales. Tu tarea es extraer "
    "el título de la canción y el artista principal. Respondé ÚNICAMENTE con un objeto "
    'JSON válido, sin texto ni explicación alrededor, con esta forma exacta: '
    '{"cancion": "<título>", "artista": "<artista>"}. '
    "Si no podés identificar una canción con seguridad, o el post no es sobre una "
    f'canción concreta, respondé exactamente con el texto: {NONE_SENTINEL}'
)

MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def _parse(text: str):
    """Normaliza la respuesta de Claude -> (cancion, artista) o None."""
    text = text.strip()
    if not text or text.upper().strip(' "') == NONE_SENTINEL:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    cancion = (data.get("cancion") or "").strip()
    artista = (data.get("artista") or "").strip()
    if not cancion:
        return None
    return (cancion, artista)


def _ask(client, content):
    resp = client.messages.create(
        model=MODEL, max_tokens=200, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return _parse(text)


def from_caption(client, caption: str):
    return _ask(client, (
        "Texto del post de Instagram:\n\n"
        f"{caption}\n\n¿De qué canción habla? Devolvé el JSON pedido."
    ))


def from_image(client, session, url: str):
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        data = base64.standard_b64encode(r.content).decode("utf-8")
    except Exception as e:
        log(f"   ! no pude bajar el thumbnail: {e}")
        return None
    return _ask(client, [
        {"type": "image", "source": {"type": "base64",
                                     "media_type": "image/jpeg", "data": data}},
        {"type": "text", "text": "¿De qué canción habla esta imagen? Devolvé el JSON pedido."},
    ])


def from_transcript(client, session, vurl: str):
    transcript = transcribe_url(session, vurl)
    if not transcript:
        return None
    return _ask(client, (
        "Transcripción del audio de un Reel de @owencutts:\n\n"
        f"{transcript}\n\n¿De qué canción habla? Devolvé el JSON pedido."
    ))


def identify(client, session: requests.Session, item: dict, use_audio: bool = True):
    """Identifica (cancion, artista, metodo) de un item del feed, en capas."""
    caption = caption_text(item)
    if caption:
        try:
            r = from_caption(client, caption)
            if r:
                return (r[0], r[1], "caption")
        except Exception as e:
            log(f"   ! error en caption: {e}")

    turl = thumbnail_url(item)
    if turl:
        try:
            r = from_image(client, session, turl)
            if r:
                return (r[0], r[1], "imagen")
        except Exception as e:
            log(f"   ! error en imagen: {e}")

    if use_audio:
        vurl = video_url(item)
        if vurl:
            try:
                r = from_transcript(client, session, vurl)
                if r:
                    return (r[0], r[1], "audio")
            except Exception as e:
                log(f"   ! error en audio: {e}")

    return ("", "", "no identificado")
