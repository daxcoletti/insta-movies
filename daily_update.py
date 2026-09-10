#!/usr/bin/env python3
"""
daily_update.py — actualización incremental de la planilla de películas.

Pensado para correr desde cron (hoy, cada 3 días). Pasos:
  1. Lista los posts del perfil (newest-first) y detecta SOLO los nuevos (los que
     no están ya en <PERFIL>_peliculas.csv). Para de paginar apenas encuentra un
     post ya conocido — normalmente una sola página. El listado lo resuelve
     ig_feed.fetch_new_items, que prueba varios endpoints en orden: si Instagram
     bloquea uno, sigue por el otro en vez de cortar.
  2. Para cada post nuevo: baja su thumbnail, parsea película/año del caption,
     resuelve el título original/inglés (Claude), busca su ficha en IMDb y el
     mejor torrent.
  3. Backfill: completa la columna IMDb (y Torrent) en las filas viejas que no la
     tengan — así la primera corrida puebla IMDb en toda la planilla.
  4. Reescribe <PERFIL>_peliculas.csv y .xlsx (columnas: Fecha, Película, Año,
     Link, IMDb, Torrent, Caption) y, si hay un remote configurado, sube la
     planilla a Google Drive con rclone.

Uso:
    export ANTHROPIC_API_KEY=sk-ant-...
    python daily_update.py [PERFIL] [--login USUARIO_IG]

Códigos de salida:
    0  OK
    1  error de configuración (falta la API key, sesión ilegible, ...)
    2  Instagram no permite listar los posts por ninguna vía (soft-block o
       rate-limit). No es un bug ni una sesión vencida: se libera solo. Para
       agregar posts mientras tanto, add_posts.py los trae de a uno por link.

Variables de entorno (las setea run_daily.sh desde .env):
    ANTHROPIC_API_KEY   (requerida)
    IG_LOGIN_USER       usuario de IG con sesión guardada (default: daxcoletti)
    DRIVE_DEST          destino rclone, p. ej. "drive:insta-movies" (default ese)
"""

import argparse
import csv
import datetime as dt
import os
import subprocess
import sys
import time
import urllib.parse

import instaloader

import ig_feed
from build_spreadsheet import parse_title_year
from find_torrents import best_torrent, clean_query, resolve_titles
from ig_feed import FeedUnavailable

COLS = ["Fecha", "Película", "Año", "Link", "IMDb", "Torrent", "Caption"]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
DEFAULT_PROFILE = "juan.amonda"


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ---------- IMDb ----------

def imdb_url(session, titles, year):
    """Devuelve la URL de IMDb del mejor match, vía la suggestion API (sin key)."""
    for title in titles:
        if not title:
            continue
        q = clean_query(title)
        if not q:
            continue
        url = f"https://v3.sg.media-imdb.com/suggestion/x/{urllib.parse.quote(q)}.json?includeVideos=0"
        try:
            d = session.get(url, headers=UA, timeout=12).json().get("d", [])
        except Exception:
            continue
        cands = [x for x in d if str(x.get("id", "")).startswith("tt")]
        if not cands:
            continue

        def score(x):
            s = 0
            if year and str(x.get("y", "")) == str(year):
                s += 100
            if x.get("qid") in ("movie", "tvMovie", "short", "video"):
                s += 10
            return s

        best = max(cands, key=score)
        return f"https://www.imdb.com/title/{best['id']}/"
    return ""


# ---------- Feed incremental ----------

def _download_thumb(L, profile, item, code):
    """Baja la portada del post. Dos vías, porque los items pueden venir de
    distintos endpoints: `from_iphone_struct` necesita la estructura completa del
    feed, así que si el post vino del fallback del perfil web (normalizado, con
    menos campos) tiramos directo a la URL de la imagen."""
    try:
        post = instaloader.Post.from_iphone_struct(L.context, item)
        L.download_post(post, target=profile.username)
        return
    except Exception as e:
        primera = f"{type(e).__name__}: {e}"

    url = ig_feed.thumbnail_url(item)
    if not url:
        log(f"   ! no pude bajar el thumbnail de {code}: {primera}")
        return
    try:
        os.makedirs(profile.username, exist_ok=True)
        taken = item.get("taken_at")
        stamp = (dt.datetime.fromtimestamp(taken, dt.timezone.utc)
                 .strftime("%Y-%m-%d_%H-%M-%S_UTC")) if taken else code
        destino = os.path.join(profile.username, f"{stamp}.jpg")
        if not os.path.exists(destino):
            r = L.context._session.get(url, headers=ig_feed.HEADERS, timeout=30)
            r.raise_for_status()
            with open(destino, "wb") as f:
                f.write(r.content)
        # El caption al lado, igual que instaloader, para extract_movies.py.
        txt = os.path.splitext(destino)[0] + ".txt"
        caption = ig_feed.caption_text(item)
        if caption and not os.path.exists(txt):
            with open(txt, "w", encoding="utf-8") as f:
                f.write(caption)
    except Exception as e:
        log(f"   ! no pude bajar el thumbnail de {code}: {primera} / directo: {e}")


def row_from_item(L, profile, item, download_thumb=True):
    code = item.get("code") or ""
    taken = item.get("taken_at")
    fecha = (dt.datetime.fromtimestamp(taken, dt.timezone.utc).strftime("%Y-%m-%d")
             if taken else "")
    caption = ((item.get("caption") or {}).get("text") or "").strip()
    titulo, anio = parse_title_year(caption)
    if download_thumb:
        _download_thumb(L, profile, item, code)
    return {
        "Fecha": fecha,
        "Película": titulo,
        "Año": anio,
        "Link": f"https://www.instagram.com/p/{code}/" if code else "",
        "IMDb": "",
        "Torrent": "",
        "Caption": caption.replace("\n", " ").strip(),
    }


# ---------- Escritura ----------

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
    ws.title = "Películas"
    ws.append(COLS)
    for c in ws[1]:
        c.font = Font(bold=True)
    link_cols = {"Link": 4, "IMDb": 5}
    for i, r in enumerate(rows, start=2):
        ws.append([r.get(c, "") for c in COLS])
        for name, col in link_cols.items():
            if r.get(name):
                cell = ws.cell(row=i, column=col)
                cell.hyperlink = r[name]
                cell.font = Font(color="0563C1", underline="single")
    for col, wdt in {"A": 12, "B": 30, "C": 7, "D": 38, "E": 34, "F": 58, "G": 70}.items():
        ws.column_dimensions[col].width = wdt
    ws.freeze_panes = "A2"
    wb.save(path)


def upload_to_drive(paths):
    dest = os.environ.get("DRIVE_DEST", "drive:insta-movies")
    ok = True
    for p in paths:
        try:
            r = subprocess.run(["rclone", "copy", p, dest, "-v"],
                               capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                log(f">> Drive: subido {os.path.basename(p)} -> {dest}")
            else:
                ok = False
                log(f">> Drive: FALLÓ subir {os.path.basename(p)} ({r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'error'})")
        except Exception as e:
            ok = False
            log(f">> Drive: error con rclone ({e})")
    if not ok:
        log(">> Drive: si el remote dice 'token expired'/'deleted_client', reconectá:")
        log("     rclone config reconnect drive:")
    return ok


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser(description="Actualización incremental diaria.")
    ap.add_argument("profile", nargs="?", default=DEFAULT_PROFILE)
    ap.add_argument("--login", default=os.environ.get("IG_LOGIN_USER", "daxcoletti"))
    ap.add_argument("--no-drive", action="store_true", help="no subir a Drive")
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
    known_codes = {r["Link"].rstrip("/").split("/")[-1] for r in existing if r.get("Link")}
    log(f">> {len(existing)} filas existentes en {csv_path}.")

    import anthropic
    import requests
    client = anthropic.Anthropic()
    session = requests.Session()

    # Sesión de instaloader + perfil.
    L = instaloader.Instaloader(dirname_pattern="{profile}", download_videos=False,
                                download_video_thumbnails=True, save_metadata=False)
    L.load_session_from_file(args.login)
    profile = instaloader.Profile.from_username(L.context, args.profile)

    # 1. Posts nuevos. Si Instagram no nos deja listar por ninguna vía, salimos con
    #    código 2: no es un bug ni una sesión vencida, y el wrapper lo notifica distinto.
    try:
        new_items = ig_feed.fetch_new_items(L, profile, known_codes)
    except FeedUnavailable as e:
        log(f">> Instagram no permite listar los posts ahora ({e}).")
        log(">> No es la sesión: los posts sueltos siguen andando. Opciones:")
        log("     - esperar (el soft-block se libera solo; cada reintento lo renueva);")
        log(f"     - agregar los que falten a mano: python add_posts.py <link> ...")
        return 2
    log(f">> Posts nuevos detectados: {len(new_items)}")
    new_rows = [row_from_item(L, profile, it) for it in reversed(new_items)]  # viejos->nuevos
    for r in new_rows:
        log(f">> nuevo {r.get('Fecha') or '?'}: {r.get('Película') or '(sin título)'}"
            f"{' (' + r['Año'] + ')' if r.get('Año') else ''}")

    all_rows = existing + new_rows

    # 2. Filas que necesitan enriquecimiento.
    #    IMDb: cualquier fila sin IMDb (la 1ª corrida hace el backfill completo).
    #    Torrent: solo posts NUEVOS — no re-busca los misses ya confirmados (es lo lento).
    need_imdb = [r for r in all_rows if not r.get("IMDb")]
    need_torrent = [r for r in new_rows if not r.get("Torrent")]
    to_resolve = {(r["Película"], r["Año"]) for r in (need_imdb + need_torrent)
                  if r.get("Película")}
    log(f">> A completar: IMDb={len(need_imdb)}, Torrent={len(need_torrent)}, "
        f"títulos a resolver={len(to_resolve)}")

    resolved = {}
    if to_resolve:
        movies = [(i, t, y) for i, (t, y) in enumerate(sorted(to_resolve))]
        idx_of = {(t, y): i for i, (t, y) in enumerate(sorted(to_resolve))}
        resmap = resolve_titles(client, movies)
        resolved = {key: resmap.get(idx_of[key], ("", "")) for key in to_resolve}

    def candidates(r):
        orig, eng = resolved.get((r["Película"], r["Año"]), ("", ""))
        return [eng, orig, r["Película"]]

    # 3. IMDb (backfill + nuevos).
    for n, r in enumerate(need_imdb, 1):
        r["IMDb"] = imdb_url(session, candidates(r), r["Año"])
        if n % 25 == 0 or n == len(need_imdb):
            log(f">> IMDb: {n}/{len(need_imdb)}")
        time.sleep(0.15)

    # 4. Torrents (normalmente solo los posts nuevos).
    for n, r in enumerate(need_torrent, 1):
        magnet, _ = best_torrent(candidates(r), r["Año"])
        r["Torrent"] = magnet or ""
        log(f">> Torrent {n}/{len(need_torrent)}: {r['Película']} -> "
            f"{'ok' if magnet else 'sin torrent'}")

    # 5. Ordenar por fecha y escribir.
    all_rows.sort(key=lambda r: r.get("Fecha", ""))
    write_csv(csv_path, all_rows)
    write_xlsx(xlsx_path, all_rows)
    log(f">> Planilla actualizada: {len(all_rows)} filas ({len(new_rows)} nuevas).")

    # 6. Drive.
    if not args.no_drive:
        upload_to_drive([xlsx_path, csv_path])

    log(">> Listo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
