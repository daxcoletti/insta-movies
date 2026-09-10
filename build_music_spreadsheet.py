#!/usr/bin/env python3
"""
build_music_spreadsheet.py — planilla de canciones del perfil @owencutts, una fila por
post. Para cada post identifica la canción en capas (caption -> imagen -> audio; ver
identify_songs.py), la canoniza con Spotify (título/artista/álbum/año/link oficiales) y,
al final, sincroniza una playlist acumulativa con todos los temas.

Columnas: Fecha · Canción · Artista · Álbum · Año · Link (post) · Spotify · Caption.

Salida:
    <PERFIL>_canciones.csv   (UTF-8 con BOM)
    <PERFIL>_canciones.xlsx  (si openpyxl está instalado)

Uso:
    export ANTHROPIC_API_KEY=sk-ant-...
    # cargá el .env con las SPOTIPY_* (o pasá --no-spotify para saltear)
    python build_music_spreadsheet.py owencutts --login <TU_USUARIO_IG>

Flags:
    --no-audio     no transcribir (solo caption + imagen) — más rápido y barato.
    --no-spotify   no canonizar ni tocar la playlist.
    --limit N      procesar solo los N posts más nuevos (para probar).
"""

import argparse
import csv
import os
import sys

import requests

import ig_feed
from identify_songs import identify

COLS = ["Fecha", "Canción", "Artista", "Álbum", "Año", "Link", "Spotify", "Caption"]
DEFAULT_PROFILE = "owencutts"


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLS})


def write_xlsx(path, rows):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        log(">> (openpyxl no instalado; salté el .xlsx)")
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Canciones"
    ws.append(COLS)
    for c in ws[1]:
        c.font = Font(bold=True)
    link_cols = {"Link": COLS.index("Link") + 1, "Spotify": COLS.index("Spotify") + 1}
    for i, r in enumerate(rows, start=2):
        ws.append([r.get(c, "") for c in COLS])
        for name, col in link_cols.items():
            if r.get(name):
                cell = ws.cell(row=i, column=col)
                cell.hyperlink = r[name]
                cell.font = Font(color="0563C1", underline="single")
    widths = {"A": 12, "B": 30, "C": 26, "D": 28, "E": 7, "F": 38, "G": 42, "H": 70}
    for col, wdt in widths.items():
        ws.column_dimensions[col].width = wdt
    ws.freeze_panes = "A2"
    wb.save(path)


def canonicalize(sp, row, song, artist):
    """Usa Spotify para rellenar Canción/Artista/Álbum/Año/Spotify. Devuelve el uri o ''."""
    match = None
    if sp is not None:
        from spotify_sync import search_track
        match = search_track(sp, song, artist)
    if match:
        row["Canción"] = match["cancion"]
        row["Artista"] = match["artista"]
        row["Álbum"] = match["album"]
        row["Año"] = match["anio"]
        row["Spotify"] = match["url"]
        return match["uri"]
    # Sin Spotify (o sin match): dejamos lo que dijo Claude.
    row["Canción"] = song
    row["Artista"] = artist
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Planilla de canciones por post + playlist.")
    ap.add_argument("profile", nargs="?", default=DEFAULT_PROFILE)
    ap.add_argument("--login", default=os.environ.get("IG_LOGIN_USER", "daxcoletti"))
    ap.add_argument("--no-audio", action="store_true", help="no transcribir audio")
    ap.add_argument("--no-spotify", action="store_true", help="no canonizar ni playlist")
    ap.add_argument("--limit", type=int, default=0,
                    help="procesar como máximo N posts NUEVOS (no vistos) en esta corrida")
    ap.add_argument("--drive", action="store_true",
                    help="subir la planilla a Google Drive con rclone")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("Error: falta ANTHROPIC_API_KEY."); return 1

    import anthropic
    client = anthropic.Anthropic()
    session = requests.Session()

    sp = None
    if not args.no_spotify:
        try:
            from spotify_sync import get_spotify
            sp = get_spotify()
        except Exception as e:
            log(f">> Spotify deshabilitado ({e}). Seguí con --no-spotify o autorizá con spotify_auth.py.")
            return 1

    try:
        L = ig_feed.build_loader(args.login, videos=False)
    except FileNotFoundError:
        log(f"No hay sesión guardada para @{args.login}. Importá cookies: source ./setup.sh {args.profile} --chrome")
        return 1
    profile = ig_feed.load_profile(L, args.profile)
    log(f">> @{profile.username} (id {profile.userid}) — armando planilla de canciones ...")

    csv_path = f"{args.profile}_canciones.csv"
    xlsx_path = f"{args.profile}_canciones.xlsx"
    seen_path = f"{args.profile}_seen.txt"
    CHECKPOINT_EVERY = 25

    def code_of(link):
        return link.rstrip("/").split("/")[-1] if link else ""

    seen = set()
    # --- Estado previo: planilla + posts vistos, para REANUDAR / incremental. ---
    # La planilla guarda solo canciones; los no-canción viven solo en seen_path, así
    # que esta corrida (y el cron) saltean todo lo ya procesado y hacen solo lo que falta.
    rows_by_link = {}
    if os.path.isfile(csv_path):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("Link") and r.get("Canción"):
                    rows_by_link[r["Link"]] = r
                    seen.add(code_of(r["Link"]))
    if os.path.isfile(seen_path):
        for ln in open(seen_path, encoding="utf-8"):
            if ln.strip():
                seen.add(ln.strip())
    if seen:
        log(f">> Reanudando: {len(rows_by_link)} canciones guardadas, "
            f"{len(seen)} posts ya vistos (se saltean).")

    def save_progress():
        with open(seen_path, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(seen)) + "\n")
        out = sorted(rows_by_link.values(), key=lambda r: r.get("Fecha", ""))
        write_csv(csv_path, out)
        write_xlsx(xlsx_path, out)
        return out

    uris = []
    nuevos = 0
    bloqueado = ""

    def feed_items():
        """Envuelve el listado para que un bloqueo de Instagram A MITAD del scan no
        tire la corrida: cortamos el recorrido y dejamos que siga el flujo normal,
        que guarda la planilla y sincroniza la playlist con lo que sí se alcanzó.
        Este script necesita el listado COMPLETO, que no tiene fallback — pero es
        reanudable por <PERFIL>_seen.txt, así que lo procesado no se pierde."""
        nonlocal bloqueado
        try:
            yield from ig_feed.iter_feed_items(L, profile)
        except ig_feed.FeedUnavailable as e:
            bloqueado = str(e)

    for item in feed_items():
        code = ig_feed.shortcode(item)
        if code and code in seen:
            continue  # ya procesado en una corrida anterior -> saltear
        row = {c: "" for c in COLS}
        row["Fecha"] = ig_feed.taken_date(item)
        row["Link"] = ig_feed.post_link(item)
        row["Caption"] = ig_feed.caption_text(item).replace("\n", " ").strip()

        song, artist, metodo = identify(client, session, item, use_audio=not args.no_audio)
        uri = ""
        if song:
            uri = canonicalize(sp, row, song, artist)
            if uri:
                uris.append(uri)
            rows_by_link[row["Link"]] = row   # solo canciones a la planilla
        if code:
            seen.add(code)
        nuevos += 1
        log(f"[{nuevos}] {row['Fecha']} {row['Canción'] or '(no identificado)'} — "
            f"{row['Artista']}  ({metodo}{', spotify' if uri else ''})")

        if nuevos % CHECKPOINT_EVERY == 0:
            save_progress()
            log(f">> checkpoint: {len(rows_by_link)} canciones guardadas.")
        if args.limit and nuevos >= args.limit:
            break

    out_rows = save_progress()
    log(f">> Planilla: {csv_path} / {xlsx_path} ({len(out_rows)} canciones; "
        f"+{nuevos} posts nuevos procesados esta corrida)")

    if sp is not None:
        from spotify_sync import (ensure_playlist, add_tracks, playlist_url,
                                  explain_error, url_to_uri)
        # uris nuevos + los de las canciones ya guardadas, para que la playlist
        # quede completa aunque hayamos reanudado.
        all_uris = [u for u in uris] + [url_to_uri(r.get("Spotify", "")) for r in out_rows]
        all_uris = [u for u in all_uris if u]
        if all_uris:
            name = os.environ.get("SPOTIFY_PLAYLIST_NAME", "Old Music Friday — Owen Cutts")
            try:
                pl_id = ensure_playlist(sp, name)
                added = add_tracks(sp, pl_id, all_uris)
                log(f">> Playlist '{name}': +{added} temas. {playlist_url(sp, pl_id)}")
            except Exception as e:
                log(f">> Playlist: FALLÓ ({e}). La planilla ya quedó guardada.")
                log(explain_error(e))

    if args.drive:
        from daily_update import upload_to_drive
        upload_to_drive([xlsx_path, csv_path])

    if bloqueado:
        log(f">> ! El scan quedó CORTADO: Instagram no permite listar más ({bloqueado}).")
        log(">> Lo procesado ya está guardado. Cuando se libere, volvé a correrlo y")
        log(f">> sigue donde quedó (se saltea lo que ya está en {seen_path}).")
        return 2

    log(f">> Listo: {len(out_rows)} canciones en la planilla.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
