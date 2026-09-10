#!/usr/bin/env python3
"""
add_posts.py — agrega posts sueltos a la planilla, por link o shortcode.

Para cuando Instagram bloquea el LISTADO de posts pero no los posts en sí. El
endpoint `/api/v1/feed/user/` que usa `daily_update.py` puede devolver
`400 {"message":"feedback_required","spam":true}` (soft-block por actividad
automatizada, a nivel cuenta+IP), mientras que `/api/v1/media/<id>/info/` sigue
respondiendo 200. Este script usa ese segundo endpoint: vos le pasás los links
que ves en el navegador y él completa las filas que falten.

Hace exactamente lo mismo que `daily_update.py` con un post nuevo (parseo del
caption, IMDb, torrent, CSV + XLSX, subida a Drive); lo único que cambia es de
dónde salen los posts.

Uso:
    python add_posts.py https://www.instagram.com/p/ABC123/ DEF456 ...
    python add_posts.py --profile juan.amonda --no-drive <links...>

Salida:
    0  planilla actualizada (o nada que agregar)
    1  error de configuración o ningún post pudo traerse
"""

import argparse
import csv
import os
import sys
import time

import instaloader
import requests

from daily_update import (
    COLS, DEFAULT_PROFILE, imdb_url, log, row_from_item, upload_to_drive,
    write_csv, write_xlsx,
)
from find_torrents import best_torrent, resolve_titles
from ig_feed import fetch_item_by_shortcode, shortcode_of


def fetch_item(L, profile, shortcode: str):
    """Trae un post por shortcode y verifica que sea del perfil correcto."""
    item = fetch_item_by_shortcode(L, shortcode)
    owner = ((item.get("user") or {}).get("username") or "").lower()
    if owner and owner != profile.username.lower():
        raise RuntimeError(f"el post es de @{owner}, no de @{profile.username}")
    return item


def main() -> int:
    ap = argparse.ArgumentParser(description="Agrega posts sueltos a la planilla, por link.")
    ap.add_argument("posts", nargs="+", help="links de Instagram o shortcodes")
    ap.add_argument("--profile", default=os.environ.get("PROFILE", DEFAULT_PROFILE))
    ap.add_argument("--login", default=os.environ.get("IG_LOGIN_USER", "daxcoletti"))
    ap.add_argument("--no-drive", action="store_true", help="no subir a Drive")
    ap.add_argument("--no-thumb", action="store_true", help="no bajar el thumbnail")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("Error: falta ANTHROPIC_API_KEY.")
        return 1

    csv_path = f"{args.profile}_peliculas.csv"
    xlsx_path = f"{args.profile}_peliculas.xlsx"

    existing = []
    if os.path.isfile(csv_path):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            existing = list(csv.DictReader(f))
    known = {r["Link"].rstrip("/").split("/")[-1] for r in existing if r.get("Link")}
    log(f">> {len(existing)} filas existentes en {csv_path}.")

    wanted, seen = [], set()
    for raw in args.posts:
        sc = shortcode_of(raw)
        if not sc:
            log(f">> ! no pude sacar el shortcode de {raw!r}, lo salteo.")
        elif sc in known:
            log(f">> {sc} ya está en la planilla, lo salteo.")
        elif sc not in seen:
            seen.add(sc)
            wanted.append(sc)
    if not wanted:
        log(">> Nada para agregar.")
        return 0

    import anthropic
    client = anthropic.Anthropic()
    session = requests.Session()

    L = instaloader.Instaloader(dirname_pattern="{profile}", download_videos=False,
                                download_video_thumbnails=True, save_metadata=False)
    L.load_session_from_file(args.login)
    profile = instaloader.Profile.from_username(L.context, args.profile)

    new_rows, failed = [], []
    for sc in wanted:
        try:
            item = fetch_item(L, profile, sc)
        except Exception as e:
            log(f">> ! {sc}: {e}")
            failed.append(sc)
            continue
        r = row_from_item(L, profile, item, download_thumb=not args.no_thumb)
        new_rows.append(r)
        log(f">> nuevo {r.get('Fecha') or '?'}: {r.get('Película') or '(sin título)'}"
            f"{' (' + r['Año'] + ')' if r.get('Año') else ''}")
        time.sleep(1)

    if not new_rows:
        log(">> No pude traer ningún post.")
        return 1

    all_rows = existing + new_rows

    # Enriquecimiento, igual que daily_update: IMDb para lo que falte, torrent solo
    # para los posts recién agregados (no se re-buscan los misses ya confirmados).
    need_imdb = [r for r in all_rows if not r.get("IMDb")]
    need_torrent = [r for r in new_rows if not r.get("Torrent")]
    to_resolve = {(r["Película"], r["Año"]) for r in (need_imdb + need_torrent)
                  if r.get("Película")}
    log(f">> A completar: IMDb={len(need_imdb)}, Torrent={len(need_torrent)}, "
        f"títulos a resolver={len(to_resolve)}")

    resolved = {}
    if to_resolve:
        ordered = sorted(to_resolve)
        movies = [(i, t, y) for i, (t, y) in enumerate(ordered)]
        idx_of = {ty: i for i, ty in enumerate(ordered)}
        resmap = resolve_titles(client, movies)
        resolved = {key: resmap.get(idx_of[key], ("", "")) for key in to_resolve}

    def candidates(r):
        orig, eng = resolved.get((r["Película"], r["Año"]), ("", ""))
        return [eng, orig, r["Película"]]

    for n, r in enumerate(need_imdb, 1):
        r["IMDb"] = imdb_url(session, candidates(r), r["Año"])
        if n % 25 == 0 or n == len(need_imdb):
            log(f">> IMDb: {n}/{len(need_imdb)}")
        time.sleep(0.15)

    for n, r in enumerate(need_torrent, 1):
        magnet, _ = best_torrent(candidates(r), r["Año"])
        r["Torrent"] = magnet or ""
        log(f">> Torrent {n}/{len(need_torrent)}: {r['Película']} -> "
            f"{'ok' if magnet else 'sin torrent'}")

    all_rows.sort(key=lambda r: r.get("Fecha", ""))
    write_csv(csv_path, all_rows)
    write_xlsx(xlsx_path, all_rows)
    log(f">> Planilla actualizada: {len(all_rows)} filas ({len(new_rows)} nuevas).")

    if not args.no_drive:
        upload_to_drive([xlsx_path, csv_path])

    if failed:
        log(f">> Quedaron sin agregar: {', '.join(failed)}")
    log(">> Listo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
