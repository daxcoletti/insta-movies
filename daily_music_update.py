#!/usr/bin/env python3
"""
daily_music_update.py — actualización incremental diaria de la planilla de canciones de
@owencutts + sincronización de la playlist de Spotify. Análogo a daily_update.py (el de
películas), pensado para cron.

Pasos:
  1. Mira el feed (newest-first) y junta SOLO los posts nuevos (los que no están en
     <PERFIL>_canciones.csv). Para de paginar apenas ve uno conocido.
  2. Por cada post nuevo: identifica la canción en capas (caption -> imagen -> audio) y
     la canoniza con Spotify (título/artista/álbum/año/link).
  3. Backfill: completa la columna Spotify en filas viejas que no la tengan.
  4. Reescribe CSV + XLSX, agrega los temas a la playlist y sube la planilla a Drive.

Uso:
    export ANTHROPIC_API_KEY=sk-ant-...
    python daily_music_update.py [PERFIL] [--login USUARIO_IG]

Variables de entorno (las setea run_daily_music.sh desde .env):
    ANTHROPIC_API_KEY, SPOTIPY_*, SPOTIFY_PLAYLIST_NAME, IG_LOGIN_USER, DRIVE_DEST
"""

import argparse
import csv
import os
import sys

import requests

import ig_feed
from build_music_spreadsheet import COLS, canonicalize, write_csv, write_xlsx
from daily_update import upload_to_drive  # reuso el subidor a Drive (rclone)
from identify_songs import identify


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def row_from_item(item):
    return {
        "Fecha": ig_feed.taken_date(item),
        "Canción": "", "Artista": "", "Álbum": "", "Año": "",
        "Link": ig_feed.post_link(item),
        "Spotify": "",
        "Caption": ig_feed.caption_text(item).replace("\n", " ").strip(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Actualización diaria de canciones + playlist.")
    ap.add_argument("profile", nargs="?", default="owencutts")
    ap.add_argument("--login", default=os.environ.get("IG_LOGIN_USER", "daxcoletti"))
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-drive", action="store_true")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("Error: falta ANTHROPIC_API_KEY."); return 1

    csv_path = f"{args.profile}_canciones.csv"
    xlsx_path = f"{args.profile}_canciones.xlsx"

    existing = []
    if os.path.isfile(csv_path):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            existing = list(csv.DictReader(f))
    # known_codes = lo que ya está en la planilla (canciones) UNIÓN el registro de
    # shortcodes vistos (incluye los no-canción), así no reprocesamos promos a diario.
    seen_path = f"{args.profile}_seen.txt"
    seen = set()
    if os.path.isfile(seen_path):
        seen = {ln.strip() for ln in open(seen_path, encoding="utf-8") if ln.strip()}
    known_codes = seen | {r["Link"].rstrip("/").split("/")[-1] for r in existing if r.get("Link")}
    log(f">> {len(existing)} filas existentes en {csv_path}; {len(seen)} posts ya vistos.")

    import anthropic
    client = anthropic.Anthropic()
    session = requests.Session()

    try:
        from spotify_sync import get_spotify
        sp = get_spotify()
    except Exception as e:
        log(f"Error: Spotify no autorizado ({e}). Corré spotify_auth.py una vez."); return 1

    try:
        L = ig_feed.build_loader(args.login, videos=False)
    except FileNotFoundError:
        log(f"No hay sesión para @{args.login}. Regenerá: source ./setup.sh {args.profile} --chrome")
        return 1
    profile = ig_feed.load_profile(L, args.profile)

    # 1-2. Posts nuevos -> identificar + canonizar.
    new_items = ig_feed.fetch_new_items(L, profile, known_codes)
    log(f">> Posts nuevos detectados: {len(new_items)}")
    new_rows, uris, new_codes = [], [], []
    for item in reversed(new_items):  # viejos -> nuevos
        new_codes.append(ig_feed.shortcode(item))
        row = row_from_item(item)
        song, artist, metodo = identify(client, session, item, use_audio=not args.no_audio)
        if song:
            uri = canonicalize(sp, row, song, artist)
            if uri:
                uris.append(uri)
            new_rows.append(row)   # solo canciones van a la planilla
        log(f">> nuevo {row['Fecha']}: {row.get('Canción') or '(sin id, no se agrega)'} — "
            f"{row.get('Artista')} ({metodo})")

    # Registrar todos los codes vistos (incl. no-canción) para no reprocesarlos.
    seen |= {c for c in new_codes if c}
    with open(seen_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(seen)) + "\n")

    all_rows = existing + new_rows

    # 3. Backfill de Spotify en filas viejas que tengan canción pero no link.
    need_spotify = [r for r in existing if r.get("Canción") and not r.get("Spotify")]
    if need_spotify:
        log(f">> Backfill Spotify: {len(need_spotify)} filas.")
        from spotify_sync import search_track
        for r in need_spotify:
            m = search_track(sp, r["Canción"], r.get("Artista", ""))
            if m:
                r["Álbum"] = r.get("Álbum") or m["album"]
                r["Año"] = r.get("Año") or m["anio"]
                r["Spotify"] = m["url"]

    # 4. Escribir, sincronizar playlist (con TODOS los uris conocidos), subir a Drive.
    all_rows.sort(key=lambda r: r.get("Fecha", ""))
    write_csv(csv_path, all_rows)
    write_xlsx(xlsx_path, all_rows)
    log(f">> Planilla actualizada: {len(all_rows)} filas ({len(new_rows)} nuevas).")

    from spotify_sync import ensure_playlist, add_tracks, playlist_url, url_to_uri, explain_error
    all_uris = list(uris)
    all_uris += [url_to_uri(r["Spotify"]) for r in all_rows if r.get("Spotify")]
    name = os.environ.get("SPOTIFY_PLAYLIST_NAME", "Old Music Friday — Owen Cutts")
    try:
        pl_id = ensure_playlist(sp, name)
        added = add_tracks(sp, pl_id, [u for u in all_uris if u])
        log(f">> Playlist '{name}': +{added} temas. {playlist_url(sp, pl_id)}")
    except Exception as e:
        log(f">> Playlist: FALLÓ ({e}). La planilla ya quedó guardada.")
        log(explain_error(e))

    if not args.no_drive:
        upload_to_drive([xlsx_path, csv_path])

    log(">> Listo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
