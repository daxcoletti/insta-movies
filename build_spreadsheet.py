#!/usr/bin/env python3
"""
build_spreadsheet.py — arma una planilla con un renglón por post del perfil:
fecha, nombre de la película, año, link al post y el caption.

Lista los posts por el mismo endpoint privado que usa download_posts.py
(/api/v1/feed/user/), porque el GraphQL que usa instaloader para paginar la
timeline está deprecado (400 "invalid request"). Ver download_posts.py.

El título y el año se sacan del caption, que en este perfil sigue el formato
"Título (AÑO) ...". Si no hay match (caption sin año), se deja el año vacío y el
título se infiere de la primera línea del caption.

Salida:
    <PERFIL>_peliculas.csv   (UTF-8 con BOM, abre bien en Excel/Sheets)
    <PERFIL>_peliculas.xlsx  (si openpyxl está instalado)

Uso:
    python build_spreadsheet.py <PERFIL> --login <USUARIO_IG>
"""

import argparse
import csv
import datetime as dt
import re
import sys
import time

import instaloader

HEADERS = {
    "X-IG-App-ID": "936619743392459",
    "X-ASBD-ID": "198387",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

# El año es el primer número de 4 dígitos que aparezca DENTRO de un paréntesis,
# así cubrimos todos los formatos del perfil: "(2024)", "(2015, Director)",
# "(The Phone Call, 2015)", "(1973, Federico Fellini)". Exigir el paréntesis evita
# confundir años que forman parte del título ("Blade Runner 2049 (2017)").
YEAR_IN_PARENS_RE = re.compile(r"\([^)]*\b(\d{4})\b[^)]*\)")


def parse_title_year(caption: str):
    """Devuelve (titulo, anio) a partir del caption."""
    if not caption:
        return ("", "")
    first_line = caption.strip().splitlines()[0]
    m = YEAR_IN_PARENS_RE.search(first_line)
    year = m.group(1) if m else ""
    # Título: lo que va antes del primer paréntesis; si no hay, antes de un guion.
    if "(" in first_line:
        title = first_line.split("(", 1)[0].strip()
    else:
        title = re.split(r"\s[-–—]\s", first_line, maxsplit=1)[0].strip()
    if not title:  # caso raro: arranca con paréntesis
        title = first_line.strip()
    return (title, year)


def iter_items(L, profile):
    max_id = None
    headers = {**HEADERS, "Referer": f"https://www.instagram.com/{profile.username}/"}
    url = f"https://www.instagram.com/api/v1/feed/user/{profile.userid}/"
    while True:
        params = {"count": 12}
        if max_id:
            params["max_id"] = max_id
        resp = L.context._session.get(
            url, headers=headers, params=params, timeout=L.context.request_timeout
        )
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("items") or []:
            yield item
        if not data.get("more_available"):
            break
        max_id = data.get("next_max_id")
        if not max_id:
            break
        time.sleep(2)


def main() -> int:
    ap = argparse.ArgumentParser(description="Planilla de películas por post.")
    ap.add_argument("profile", help="Perfil de Instagram.")
    ap.add_argument("--login", required=True, help="Tu usuario de IG (sesión guardada).")
    args = ap.parse_args()

    L = instaloader.Instaloader()
    try:
        L.load_session_from_file(args.login)
    except FileNotFoundError:
        print(f"No hay sesión guardada para @{args.login}.", file=sys.stderr)
        return 1
    profile = instaloader.Profile.from_username(L.context, args.profile)
    print(f">> @{profile.username} (id {profile.userid}) — armando planilla ...", file=sys.stderr)

    rows = []  # (fecha, titulo, anio, link, caption)
    for item in iter_items(L, profile):
        code = item.get("code") or ""
        taken = item.get("taken_at")
        fecha = (
            dt.datetime.fromtimestamp(taken, dt.timezone.utc).strftime("%Y-%m-%d")
            if taken else ""
        )
        cap_obj = item.get("caption") or {}
        caption = (cap_obj.get("text") or "").strip()
        titulo, anio = parse_title_year(caption)
        link = f"https://www.instagram.com/p/{code}/" if code else ""
        rows.append((fecha, titulo, anio, link, caption.replace("\n", " ").strip()))

    rows.sort(key=lambda r: r[0])  # por fecha ascendente
    headers_row = ["Fecha", "Película", "Año", "Link", "Caption"]

    csv_path = f"{args.profile}_peliculas.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers_row)
        w.writerows(rows)
    print(f">> CSV: {csv_path} ({len(rows)} filas)", file=sys.stderr)

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook()
        ws = wb.active
        ws.title = "Películas"
        ws.append(headers_row)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append(list(r))
        # Links clicables y anchos cómodos.
        for i, r in enumerate(rows, start=2):
            if r[3]:
                cell = ws.cell(row=i, column=4)
                cell.hyperlink = r[3]
                cell.font = Font(color="0563C1", underline="single")
        widths = {"A": 12, "B": 34, "C": 7, "D": 42, "E": 80}
        for col, wdt in widths.items():
            ws.column_dimensions[col].width = wdt
        ws.freeze_panes = "A2"
        xlsx_path = f"{args.profile}_peliculas.xlsx"
        wb.save(xlsx_path)
        print(f">> XLSX: {xlsx_path}", file=sys.stderr)
    except ImportError:
        print(">> (openpyxl no instalado; salté el .xlsx)", file=sys.stderr)

    sin_anio = sum(1 for r in rows if not r[2])
    print(f">> Listo: {len(rows)} posts, {sin_anio} sin año detectado.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
