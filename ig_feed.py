#!/usr/bin/env python3
"""
ig_feed.py — helpers compartidos para listar posts de un perfil de Instagram por el
endpoint privado /api/v1/feed/user/ (el workaround al GraphQL deprecado; ver
download_posts.py) y para sacar de cada item del feed lo que necesitamos: caption,
fecha, shortcode, y las URLs del thumbnail y del video.

Lo usa el pipeline de MÚSICA (identify_songs.py, build_music_spreadsheet.py,
daily_music_update.py). El pipeline de películas tiene su propia copia histórica de
estos helpers en download_posts.py / build_spreadsheet.py; acá centralizamos para no
volver a duplicarlos.
"""

import datetime as dt
import time

import instaloader

# Headers de la web de Instagram. Sin el X-IG-App-ID (y un User-Agent real) el
# endpoint privado responde 400/403. Es la misma constante que download_posts.HEADERS.
HEADERS = {
    "X-IG-App-ID": "936619743392459",
    "X-ASBD-ID": "198387",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


def build_loader(login: str, videos: bool = False) -> instaloader.Instaloader:
    """Crea un Instaloader con la sesión guardada cargada. videos=True habilita la
    bajada de videos (el pipeline de música baja el mp4 on-demand para transcribir)."""
    L = instaloader.Instaloader(
        dirname_pattern="{profile}",
        download_videos=videos,
        download_video_thumbnails=True,
        save_metadata=False,
    )
    L.load_session_from_file(login)  # FileNotFoundError si no hay sesión: que suba.
    return L


def load_profile(L: instaloader.Instaloader, username: str) -> instaloader.Profile:
    # No tocamos profile.mediacount: dispara metadata vía el GraphQL roto.
    return instaloader.Profile.from_username(L.context, username)


def _feed_url(profile: instaloader.Profile) -> str:
    return f"https://www.instagram.com/api/v1/feed/user/{profile.userid}/"


def iter_feed_items(L: instaloader.Instaloader, profile: instaloader.Profile):
    """Itera (newest-first) los items crudos del feed del perfil, paginando."""
    headers = {**HEADERS, "Referer": f"https://www.instagram.com/{profile.username}/"}
    url = _feed_url(profile)
    max_id = None
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
        time.sleep(2)  # cortesía / anti rate-limit entre páginas


def fetch_new_items(L, profile, known_codes):
    """Items del feed cuyo shortcode no está en known_codes. Para de paginar apenas
    aparece un post ya conocido (en un día normal, una sola request)."""
    headers = {**HEADERS, "Referer": f"https://www.instagram.com/{profile.username}/"}
    url = _feed_url(profile)
    new, max_id = [], None
    while True:
        params = {"count": 12}
        if max_id:
            params["max_id"] = max_id
        resp = L.context._session.get(
            url, headers=headers, params=params, timeout=L.context.request_timeout
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items") or []
        page_new = [it for it in items if it.get("code") not in known_codes]
        new.extend(page_new)
        if len(page_new) < len(items):       # apareció un post ya conocido
            break
        if not data.get("more_available"):
            break
        max_id = data.get("next_max_id")
        if not max_id:
            break
        time.sleep(2)
    return new


# ---------- Lectura de campos de un item del feed ----------

def shortcode(item: dict) -> str:
    return item.get("code") or ""


def post_link(item: dict) -> str:
    code = shortcode(item)
    return f"https://www.instagram.com/p/{code}/" if code else ""


def taken_date(item: dict) -> str:
    taken = item.get("taken_at")
    if not taken:
        return ""
    return dt.datetime.fromtimestamp(taken, dt.timezone.utc).strftime("%Y-%m-%d")


def caption_text(item: dict) -> str:
    return ((item.get("caption") or {}).get("text") or "").strip()


def thumbnail_url(item: dict) -> str:
    """URL de la imagen de portada (mejor candidato). Sirve tanto para fotos como
    para la portada de un Reel."""
    cands = (item.get("image_versions2") or {}).get("candidates") or []
    if cands:
        return cands[0].get("url") or ""
    # Carrusel: la portada está en el primer hijo.
    children = (item.get("carousel_media") or [])
    if children:
        cc = (children[0].get("image_versions2") or {}).get("candidates") or []
        if cc:
            return cc[0].get("url") or ""
    return ""


def video_url(item: dict) -> str:
    """URL del mp4 del Reel/video (la mejor versión disponible), o '' si no es video."""
    versions = item.get("video_versions") or []
    if versions:
        return versions[0].get("url") or ""
    children = (item.get("carousel_media") or [])
    for ch in children:
        vv = ch.get("video_versions") or []
        if vv:
            return vv[0].get("url") or ""
    return ""
