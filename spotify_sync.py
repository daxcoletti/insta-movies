#!/usr/bin/env python3
"""
spotify_sync.py — integración con Spotify. Cumple doble función:

  1. CANONIZA cada canción identificada (busca el track real y devuelve título,
     artista, álbum, año y el link de Spotify oficiales) — reemplaza el rol que en el
     pipeline de películas cumplían IMDb/torrent.
  2. Mantiene una PLAYLIST acumulativa con todos los temas.

Auth: OAuth Authorization Code (necesita modificar playlists del usuario). El token se
cachea en .spotify_cache (gitignored) y spotipy lo refresca solo, así el cron corre
headless. La primera vez hay que autorizar en el navegador: `python spotify_auth.py`.

Variables de entorno (en .env):
    SPOTIPY_CLIENT_ID
    SPOTIPY_CLIENT_SECRET
    SPOTIPY_REDIRECT_URI      (p. ej. http://127.0.0.1:8888/callback)
    SPOTIFY_PLAYLIST_NAME     (default: "Old Music Friday — Owen Cutts")
"""

import os
import sys

CACHE_PATH = ".spotify_cache"
SCOPE = "playlist-modify-public playlist-modify-private playlist-read-private"
DEFAULT_PLAYLIST = "Old Music Friday — Owen Cutts"


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def get_spotify(open_browser: bool = False):
    """Devuelve un cliente spotipy autenticado. open_browser=True solo en el paso de
    autorización inicial; el cron lo deja en False (usa el token cacheado)."""
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    for var in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI"):
        if not os.environ.get(var):
            raise RuntimeError(
                f"falta la variable de entorno {var}. Cargá el .env con las "
                "credenciales de la app de Spotify (ver CLAUDE.md)."
            )
    auth = SpotifyOAuth(
        scope=SCOPE,
        cache_path=CACHE_PATH,
        open_browser=open_browser,
    )
    # retries=0: ante un 429 spotipy tira excepción al instante (la capturamos y
    # seguimos) en vez de DORMIR el 'Retry-After' (que Spotify puede devolver en horas
    # y colgaría el proceso). requests_timeout acota cuelgues de red.
    return spotipy.Spotify(auth_manager=auth, requests_timeout=15, retries=0)


# ---------- Búsqueda / canonicalización ----------

def search_track(sp, song: str, artist: str):
    """Busca el mejor track. Devuelve dict con uri/cancion/artista/album/anio/url, o None."""
    if not song:
        return None
    queries = []
    if artist:
        queries.append(f'track:{song} artist:{artist}')
        queries.append(f"{song} {artist}")
    queries.append(f'track:{song}')
    queries.append(song)

    seen = set()
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        try:
            res = sp.search(q=q, type="track", limit=5)
        except Exception as e:
            log(f"   ! Spotify search error ({q!r}): {e}")
            continue
        items = (res.get("tracks") or {}).get("items") or []
        if not items:
            continue
        t = items[0]
        album = t.get("album") or {}
        return {
            "uri": t.get("uri", ""),
            "cancion": t.get("name", song),
            "artista": ", ".join(a.get("name", "") for a in t.get("artists") or []) or artist,
            "album": album.get("name", ""),
            "anio": (album.get("release_date") or "")[:4],
            "url": (t.get("external_urls") or {}).get("spotify", ""),
        }
    return None


# ---------- Playlist ----------

def ensure_playlist(sp, name: str):
    """Devuelve el id de la playlist `name` del usuario; la crea (privada) si no existe."""
    me = sp.me()
    user_id = me["id"]
    results = sp.current_user_playlists(limit=50)
    while results:
        for pl in results.get("items", []):
            if pl["name"] == name and (pl.get("owner") or {}).get("id") == user_id:
                return pl["id"]
        results = sp.next(results) if results.get("next") else None
    # Migración de Spotify del 11-feb-2026: crear playlist es POST /v1/me/playlists.
    # El helper de spotipy (user_playlist_create) usa el endpoint viejo
    # /v1/users/{id}/playlists, que ahora responde 403. Por eso llamamos al nuevo.
    pl = sp._post("me/playlists", payload={
        "name": name,
        "public": False,
        "description": "Temas comentados por @owencutts — generado automáticamente.",
    })
    log(f">> Playlist creada: {name}")
    return pl["id"]


def existing_uris(sp, playlist_id: str) -> set:
    # Migración de Spotify: en los items de playlist el track ahora viene bajo "item"
    # (antes "track"). Pedimos ambos campos y aceptamos cualquiera de los dos.
    uris = set()
    results = sp.playlist_items(
        playlist_id, fields="items.item.uri,items.track.uri,next", limit=100)
    while results:
        for it in results.get("items", []):
            tr = it.get("item") or it.get("track") or {}
            if tr.get("uri"):
                uris.add(tr["uri"])
        results = sp.next(results) if results.get("next") else None
    return uris


def add_tracks(sp, playlist_id: str, uris: list) -> int:
    """Agrega los uris que no estén ya en la playlist. Devuelve cuántos agregó."""
    have = existing_uris(sp, playlist_id)
    nuevos = [u for u in dict.fromkeys(uris) if u and u not in have]  # dedup, preserva orden
    # Migración 11-feb-2026: agregar tracks es POST /v1/playlists/{id}/items
    # (antes /tracks, que ahora da 403). Salteamos el helper viejo de spotipy.
    for i in range(0, len(nuevos), 100):  # la API acepta de a 100
        sp._post(f"playlists/{playlist_id}/items", payload={"uris": nuevos[i:i + 100]})
    return len(nuevos)


def explain_error(e: Exception) -> str:
    """Pista accionable para los errores típicos de Spotify."""
    if "403" in str(e):
        return (
            "   Pista: 403 al escribir con los scopes correctos suele ser que la app está "
            "en Development Mode y tu cuenta no está en la allowlist. Agregala en el "
            "dashboard → tu app → Settings → User Management (nombre + email de la cuenta)."
        )
    if "429" in str(e):
        return "   Pista: 429 = rate limit de Spotify; reintentá en unos minutos."
    return ""


def url_to_uri(url: str) -> str:
    """Convierte una URL de track (open.spotify.com/track/<id>) a uri (spotify:track:<id>)."""
    if not url or "/track/" not in url:
        return ""
    tid = url.split("/track/", 1)[1].split("?", 1)[0].strip("/")
    return f"spotify:track:{tid}" if tid else ""


def playlist_url(sp, playlist_id: str) -> str:
    try:
        pl = sp.playlist(playlist_id, fields="external_urls.spotify")
        return (pl.get("external_urls") or {}).get("spotify", "")
    except Exception:
        return ""
