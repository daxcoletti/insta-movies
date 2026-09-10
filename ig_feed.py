#!/usr/bin/env python3
"""
ig_feed.py — helpers compartidos para listar posts de un perfil de Instagram y para
sacar de cada item lo que necesitamos: caption, fecha, shortcode, y las URLs del
thumbnail y del video.

Lo usan los DOS pipelines (películas y música). Acá vive toda la lógica de red
contra Instagram, para que un cambio del lado de ellos se arregle en un solo lugar.

Instagram viene rompiendo una vía por vez, así que el listado es una CADENA DE
FALLBACK y no un endpoint fijo (`fetch_new_items` los prueba en orden):

  1. `/api/v1/feed/user/<id>/`      — el workaround al GraphQL deprecado (ver
                                      download_posts.py). Pagina, es el más completo.
  2. `/api/v1/users/web_profile_info/` — devuelve los ~12 posts más recientes sin
                                      paginar. Alcanza de sobra para el incremental
                                      (el perfil publica ~1 post por día), y responde
                                      cuando el feed está bloqueado.

Historial de lo que se fue rompiendo, y por qué la cadena está así:

  - `Profile.get_posts()` de instaloader usa un doc_id de GraphQL que Instagram
    deprecó: devuelve `400 "invalid request"`. No se arregla actualizando.
  - El endpoint del feed puede devolver `400 {"message":"feedback_required",
    "spam":true}`: un soft-block por actividad automatizada, a nivel CUENTA+IP.
    No lo arregla regenerar la sesión ni reintentar — al contrario, cada request
    renueva el flag. Por eso `FeedUnavailable` NO se reintenta: se cae al
    fallback y, si tampoco hay, se aborta limpio para que el cron avise sin ruido.
  - `/api/v1/media/<media_id>/info/` sigue respondiendo 200 aun con el feed
    bloqueado: es la vía para traer posts sueltos (`fetch_item_by_shortcode`,
    que usa add_posts.py para agregar posts a mano por link).
"""

import datetime as dt
import random
import sys
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


class FeedUnavailable(RuntimeError):
    """Instagram no nos deja listar los posts ahora mismo.

    Es distinto de "el perfil no existe" o "la sesión venció": la sesión puede estar
    perfecta y estas mismas credenciales seguir sirviendo para traer posts sueltos.
    `reason` es 'feedback_required' (soft-block por actividad automatizada),
    'rate_limited' (429) o 'http_<código>'.
    """

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _feed_url(profile: instaloader.Profile) -> str:
    return f"https://www.instagram.com/api/v1/feed/user/{profile.userid}/"


def _looks_blocked(payload) -> str:
    """Devuelve el motivo del soft-block si el cuerpo lo delata, o ''.

    Instagram manda esto con status 400, así que `raise_for_status()` lo tapaba
    detrás de un HTTPError genérico y parecía un bug nuestro.
    """
    if not isinstance(payload, dict):
        return ""
    msg = str(payload.get("message") or "")
    if msg == "feedback_required" or payload.get("spam") is True:
        return "feedback_required"
    if "checkpoint" in msg:
        return "checkpoint_required"
    return ""


def _get_json(L, url, *, headers, params=None, tries: int = 3):
    """GET con reintentos y backoff. Distingue tres desenlaces:

      - OK           -> devuelve el JSON.
      - bloqueado    -> FeedUnavailable AL INSTANTE, sin reintentar: cada request
                        extra renueva el flag y no cambia el resultado.
      - transitorio  -> reintenta con backoff exponencial + jitter (429 y 5xx).
    """
    delay = 5.0
    last = ""
    for intento in range(1, tries + 1):
        try:
            resp = L.context._session.get(
                url, headers=headers, params=params, timeout=L.context.request_timeout
            )
        except Exception as e:                      # red caída, DNS, timeout
            last = f"{type(e).__name__}: {e}"
            if intento == tries:
                raise FeedUnavailable("network", last)
            time.sleep(delay + random.uniform(0, 2)); delay *= 2
            continue

        try:
            payload = resp.json()
        except ValueError:
            payload = None

        motivo = _looks_blocked(payload)
        if motivo:
            raise FeedUnavailable(motivo, str(payload)[:200])

        if resp.status_code == 200:
            return payload if payload is not None else {}

        last = f"HTTP {resp.status_code}: {resp.text[:160]}"
        # 429 y 5xx pueden ser pasajeros; el resto no mejora reintentando.
        if resp.status_code != 429 and resp.status_code < 500:
            raise FeedUnavailable(f"http_{resp.status_code}", last)
        if intento == tries:
            raise FeedUnavailable(
                "rate_limited" if resp.status_code == 429 else f"http_{resp.status_code}",
                last,
            )
        time.sleep(delay + random.uniform(0, 2)); delay *= 2

    raise FeedUnavailable("unknown", last)


def iter_feed_items(L: instaloader.Instaloader, profile: instaloader.Profile):
    """Itera (newest-first) los items crudos del feed del perfil, paginando.

    Es la vía COMPLETA (recorre todo el perfil), así que no tiene fallback: el
    endpoint de perfil web solo devuelve los ~12 últimos. Si el feed está
    bloqueado levanta FeedUnavailable y quien llama decide qué hacer.
    """
    headers = {**HEADERS, "Referer": f"https://www.instagram.com/{profile.username}/"}
    url = _feed_url(profile)
    max_id = None
    while True:
        params = {"count": 12}
        if max_id:
            params["max_id"] = max_id
        data = _get_json(L, url, headers=headers, params=params)
        for item in data.get("items") or []:
            yield item
        if not data.get("more_available"):
            break
        max_id = data.get("next_max_id")
        if not max_id:
            break
        time.sleep(2 + random.uniform(0, 1))  # cortesía / anti rate-limit entre páginas


def _new_via_feed(L, profile, known_codes):
    """Estrategia 1: el feed privado. Pagina hasta encontrar un post ya conocido."""
    headers = {**HEADERS, "Referer": f"https://www.instagram.com/{profile.username}/"}
    url = _feed_url(profile)
    new, max_id = [], None
    while True:
        params = {"count": 12}
        if max_id:
            params["max_id"] = max_id
        data = _get_json(L, url, headers=headers, params=params)
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
        time.sleep(2 + random.uniform(0, 1))
    return new


def _normalize_web_node(node: dict) -> dict:
    """Nodo de web_profile_info -> la misma forma que un item del feed.

    Así todo lo de abajo (shortcode/taken_date/caption_text/thumbnail_url/video_url
    y row_from_item) funciona igual venga de donde venga el post.
    """
    cap_edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
    texto = cap_edges[0]["node"]["text"] if cap_edges else ""
    item = {
        "code": node.get("shortcode") or "",
        "taken_at": node.get("taken_at_timestamp"),
        "caption": {"text": texto},
        "_source": "web_profile_info",
    }
    if node.get("display_url"):
        item["image_versions2"] = {"candidates": [{"url": node["display_url"]}]}
    if node.get("video_url"):
        item["video_versions"] = [{"url": node["video_url"]}]
    return item


def _new_via_web_profile(L, profile, known_codes):
    """Estrategia 2: `/api/v1/users/web_profile_info/`.

    Devuelve solo los ~12 posts más recientes y no pagina, pero para el incremental
    alcanza (el perfil publica ~1 post por día) y sigue respondiendo cuando el feed
    está bloqueado. Si TODOS los que trae son nuevos puede haber más atrás: se avisa,
    porque en ese caso conviene esperar a que vuelva el feed o usar add_posts.py.
    """
    headers = {**HEADERS, "Referer": f"https://www.instagram.com/{profile.username}/"}
    data = _get_json(
        L, "https://www.instagram.com/api/v1/users/web_profile_info/",
        headers=headers, params={"username": profile.username},
    )
    media = (((data.get("data") or {}).get("user") or {})
             .get("edge_owner_to_timeline_media") or {})
    nodes = [e.get("node") or {} for e in (media.get("edges") or [])]
    if not nodes:
        raise FeedUnavailable("empty", "web_profile_info no devolvió posts")

    nuevos = [_normalize_web_node(n) for n in nodes
              if (n.get("shortcode") or "") not in known_codes]
    if nuevos and len(nuevos) == len(nodes):
        _log(f">> ! ojo: los {len(nodes)} posts que devuelve el perfil web son nuevos; "
             f"puede haber más atrás que este fallback no alcanza a ver.")
    return nuevos


# Orden de la cadena: primero el más completo, después el que sobrevive al bloqueo.
_ESTRATEGIAS = (
    ("feed privado", _new_via_feed),
    ("perfil web", _new_via_web_profile),
)


def fetch_new_items(L, profile, known_codes):
    """Posts del perfil que no están en known_codes, newest-first.

    Prueba las estrategias en orden y se queda con la primera que responda. Solo
    levanta FeedUnavailable si fallan TODAS — así un bloqueo de un endpoint no
    corta la corrida.
    """
    fallos = []
    for nombre, estrategia in _ESTRATEGIAS:
        try:
            items = estrategia(L, profile, known_codes)
        except FeedUnavailable as e:
            _log(f">> {nombre}: no disponible ({e.reason}).")
            fallos.append(f"{nombre}={e.reason}")
            continue
        if fallos:                      # avisamos que entró por un camino alternativo
            _log(f">> Listado resuelto por '{nombre}' tras fallar: {', '.join(fallos)}")
        return items
    raise FeedUnavailable("todas", "; ".join(fallos))


# ---------- Posts sueltos (funciona aun con el feed bloqueado) ----------

# Alfabeto base64-url con el que Instagram codifica el media_id en el shortcode.
_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def shortcode_of(arg: str) -> str:
    """Acepta un shortcode pelado o cualquier URL de post/reel y devuelve el shortcode."""
    s = arg.strip().split("?")[0].rstrip("/")
    for marker in ("/p/", "/reel/", "/reels/", "/tv/"):
        if marker in s:
            s = s.split(marker, 1)[1]
            break
    return s.split("/")[0]


def media_id_of(shortcode_: str) -> int:
    """shortcode -> media_id numérico (base64-url posicional)."""
    n = 0
    for ch in shortcode_:
        n = n * 64 + _ALPHA.index(ch)
    return n


def fetch_item_by_shortcode(L, shortcode_: str) -> dict:
    """Trae un post por `/api/v1/media/<id>/info/`, que responde aun con el feed
    bloqueado. Devuelve el item con la misma forma que los del feed."""
    url = f"https://www.instagram.com/api/v1/media/{media_id_of(shortcode_)}/info/"
    headers = {**HEADERS, "Referer": f"https://www.instagram.com/p/{shortcode_}/"}
    data = _get_json(L, url, headers=headers)
    items = data.get("items") or []
    if not items:
        raise FeedUnavailable("empty", f"{shortcode_}: la respuesta no trajo items")
    return items[0]


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
