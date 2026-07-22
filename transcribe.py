#!/usr/bin/env python3
"""
transcribe.py — capa 3 de la identificación: baja el mp4 de un Reel y lo transcribe
para sacar el nombre de la canción de lo que Owen DICE cuando no está escrito.

Reutiliza el enfoque de ~dax/dev/meet-transcriptions (en el equipo 'agente'): rota
entre proveedores cloud para esquivar rate-limits, en este orden:
    Groq (whisper-large-v3) -> Deepgram (nova-3) -> Gladia
El primero que responda gana. Las keys se leen del entorno (las setea run_daily_music.sh
desde .env): GROQ_API_KEY, DEEPGRAM_API_KEY, GLADIA_API_KEY. Solo hace falta una.

Los Reels son cortos, así que no segmentamos: se extrae el audio con ffmpeg a un mp3
chico (mono) y se manda entero. Si ffmpeg no está, se manda el mp4 tal cual.

La Claude API no procesa audio: por eso transcribimos primero y Claude lee el texto.
"""

import os
import subprocess
import sys
import tempfile

import requests

from ig_feed import HEADERS

def _collect_groq_keys():
    """Junta todas las keys de Groq disponibles para rotar: GROQ_API_KEY (admite varias
    separadas por coma) + GROQ_API_KEY_2, _3, ... Devuelve la lista deduplicada."""
    keys = [k.strip() for k in (os.environ.get("GROQ_API_KEY") or "").split(",") if k.strip()]
    for i in range(2, 9):
        k = (os.environ.get(f"GROQ_API_KEY_{i}") or "").strip()
        if k:
            keys.append(k)
    out, seen = [], set()
    for k in keys:
        if k not in seen:
            seen.add(k); out.append(k)
    return out


GROQ_API_KEYS = _collect_groq_keys()
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")
GLADIA_API_KEY = os.environ.get("GLADIA_API_KEY")

# Último fallback: whisper CLI de OpenAI instalado en el equipo 'agente' (sin API, sin
# límite de cuota). Se usa solo si las 3 APIs cloud fallan/se agotan. Vacío = deshabilitado.
WHISPER_SSH_HOST = os.environ.get("WHISPER_SSH_HOST", "agente")
WHISPER_SSH_MODEL = os.environ.get("WHISPER_SSH_MODEL", "large-v3-turbo")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def download_video(session: requests.Session, url: str, dest: str) -> bool:
    """Baja el mp4 a `dest`. Un User-Agent de navegador evita rechazos esporádicos."""
    try:
        with session.get(url, headers={"User-Agent": HEADERS["User-Agent"]},
                          stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
        return os.path.getsize(dest) > 0
    except Exception as e:
        log(f"   ! no pude bajar el video: {e}")
        return False


def extract_audio(src_mp4: str, dst_mp3: str) -> bool:
    """Extrae el audio a un mp3 mono y liviano con ffmpeg. False si ffmpeg falla/no está."""
    cmd = ["ffmpeg", "-i", src_mp4, "-vn", "-ac", "1", "-q:a", "5", dst_mp3, "-y"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode == 0 and os.path.getsize(dst_mp3) > 0
    except (FileNotFoundError, OSError):
        return False


# ---------- Proveedores ----------

def _groq(path: str):
    """Prueba cada key de Groq hasta que una responda. Devuelve el texto, o
    'RATE_LIMIT' si TODAS están rate-limited (para saltar a Deepgram), o None."""
    if not GROQ_API_KEYS:
        return None
    limited = 0
    for n, key in enumerate(GROQ_API_KEYS, 1):
        try:
            with open(path, "rb") as f:
                res = requests.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    files={"file": f, "model": (None, "whisper-large-v3")},
                    timeout=120,
                )
        except Exception as e:
            log(f"   ! Groq error (key {n}): {e}")
            continue
        if res.status_code == 429:
            limited += 1
            continue  # esta key agotada; probar la siguiente
        if res.status_code == 200:
            return res.json().get("text")
        return None  # error no-429: cortar y dejar que siga el próximo proveedor
    return "RATE_LIMIT" if limited else None


def _deepgram(path: str):
    if not DEEPGRAM_API_KEY:
        return None
    try:
        url = ("https://api.deepgram.com/v1/listen"
               "?smart_format=true&language=multi&model=nova-3")
        with open(path, "rb") as f:
            res = requests.post(
                url,
                headers={"Authorization": f"Token {DEEPGRAM_API_KEY}",
                         "Content-Type": "audio/mpeg"},
                data=f, timeout=120,
            )
        if res.status_code != 200:
            return None
        alt = (res.json().get("results", {}).get("channels", [{}])[0]
               .get("alternatives", [{}])[0])
        return alt.get("transcript")
    except Exception as e:
        log(f"   ! Deepgram error: {e}")
        return None


def _gladia(path: str):
    if not GLADIA_API_KEY:
        return None
    import time
    headers = {"x-gladia-key": GLADIA_API_KEY}
    try:
        with open(path, "rb") as f:
            up = requests.post("https://api.gladia.io/v2/upload", headers=headers,
                               files={"audio": f}, timeout=120)
        if up.status_code != 200:
            return None
        audio_url = up.json().get("audio_url")
        if not audio_url:
            return None
        res = requests.post(
            "https://api.gladia.io/v2/pre-recorded",
            headers={**headers, "Content-Type": "application/json"},
            json={"audio_url": audio_url,
                  "language_config": {"languages": ["en", "es"], "code_switching": True}},
            timeout=60,
        )
        if res.status_code not in (200, 201):
            return None
        res_url = res.json().get("result_url")
        for _ in range(120):
            poll = requests.get(res_url, headers=headers, timeout=30).json()
            if poll.get("status") == "done":
                return poll.get("result", {}).get("transcription", {}).get("full_transcript")
            if poll.get("status") == "error":
                return None
            time.sleep(2)
    except Exception as e:
        log(f"   ! Gladia error: {e}")
    return None


def _agente_whisper(path: str):
    """Fallback final: transcribe en el equipo 'agente' con el whisper CLI de OpenAI
    (modelo large-v3-turbo). Pipea el audio por SSH a un temp remoto, corre whisper y
    trae el .txt. Sin API ni límite de cuota; solo se llama si lo cloud falló."""
    if not WHISPER_SSH_HOST:
        return None
    remote = (
        'f=$(mktemp --suffix=.mp3); cat > "$f"; d=$(mktemp -d); '
        f'~/.local/bin/whisper "$f" --model {WHISPER_SSH_MODEL} '
        '--output_format txt --output_dir "$d" >/dev/null 2>&1; '
        'cat "$d"/*.txt 2>/dev/null; rm -rf "$f" "$d"'
    )
    try:
        with open(path, "rb") as fh:
            r = subprocess.run(["ssh", WHISPER_SSH_HOST, remote],
                               stdin=fh, capture_output=True, timeout=900)
        out = r.stdout.decode("utf-8", errors="replace").strip()
        if out:
            log(f"   · transcripción vía agente (whisper {WHISPER_SSH_MODEL}).")
        return out or None
    except Exception as e:
        log(f"   ! agente whisper error: {e}")
        return None


def transcribe_file(path: str) -> str:
    """Transcribe un archivo de audio rotando proveedores. '' si todos fallan.
    Orden: Groq -> Deepgram -> Gladia (cloud) -> whisper en 'agente' (sin cuota)."""
    for name, fn in (("groq", _groq), ("deepgram", _deepgram), ("gladia", _gladia),
                     ("agente", _agente_whisper)):
        result = fn(path)
        if result == "RATE_LIMIT":
            log(f"   ! {name} rate-limit; pruebo el siguiente.")
            continue
        if result:
            return result.strip()
    return ""


def transcribe_url(session: requests.Session, video_url: str) -> str:
    """Devuelve la transcripción del audio del Reel (string), o '' si falla."""
    if not video_url:
        return ""
    if not (GROQ_API_KEYS or DEEPGRAM_API_KEY or GLADIA_API_KEY or WHISPER_SSH_HOST):
        log("   ! sin transcriptores (ni keys cloud ni WHISPER_SSH_HOST); salto la capa de audio.")
        return ""
    with tempfile.TemporaryDirectory() as td:
        mp4 = os.path.join(td, "reel.mp4")
        if not download_video(session, video_url, mp4):
            return ""
        mp3 = os.path.join(td, "reel.mp3")
        audio_path = mp3 if extract_audio(mp4, mp3) else mp4  # fallback: mandar el mp4
        return transcribe_file(audio_path)
