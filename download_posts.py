#!/usr/bin/env python3
"""
download_posts.py — descarga los posts (imágenes + caption) de un perfil de
Instagram usando la sesión ya guardada por instaloader.

Por qué existe: a mediados de 2026 el `query_hash`/`doc_id` que instaloader usa
en `Profile.get_posts()` para paginar la timeline de un perfil quedó deprecado, y
Instagram responde `400 Bad Request - "invalid request"` en `/graphql/query`. El
login, la resolución del perfil y la descarga de cada post siguen funcionando; lo
único roto es el listado. Este script lista los posts por el endpoint privado
`/api/v1/feed/user/<userid>/` (el mismo que usa la app/web) y delega la descarga
de imágenes y captions en `Instaloader.download_post`, así la salida es idéntica
a la de `instaloader` (imágenes en ./<PERFIL>/ + un .txt con el caption).

Ref: https://github.com/instaloader/instaloader/issues/2689

Uso:
    python download_posts.py <PERFIL> --login <USUARIO_IG>
"""

import argparse
import sys
import time

import instaloader

# Headers de la web de Instagram. El X-IG-App-ID es el de instagram.com; sin él
# (y sin un User-Agent de navegador real) el endpoint privado responde 400/403.
HEADERS = {
    "X-IG-App-ID": "936619743392459",
    "X-ASBD-ID": "198387",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


def iter_profile_posts_v1(L: instaloader.Instaloader, profile: instaloader.Profile):
    """Itera los posts del perfil vía /api/v1/feed/user/ (fallback al GraphQL roto)."""
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
            yield instaloader.Post.from_iphone_struct(L.context, item)
        if not data.get("more_available"):
            break
        max_id = data.get("next_max_id")
        if not max_id:
            break
        time.sleep(2)  # cortesía / anti rate-limit entre páginas


def main() -> int:
    ap = argparse.ArgumentParser(description="Descarga posts de un perfil (fallback v1).")
    ap.add_argument("profile", help="Perfil de Instagram a descargar.")
    ap.add_argument("--login", required=True, help="Tu usuario de IG (sesión guardada).")
    args = ap.parse_args()

    # El perfil publica casi todo como Reels (videos). No bajamos el video, pero SÍ
    # su thumbnail (portada): así queda un .jpg por post para que extract_movies.py
    # —que itera sobre imágenes— tenga su imagen y, de paso, el fallback de Vision.
    L = instaloader.Instaloader(
        dirname_pattern="{profile}",
        download_videos=False,
        download_video_thumbnails=True,
        save_metadata=False,
    )

    try:
        L.load_session_from_file(args.login)
    except FileNotFoundError:
        print(
            f"No hay sesión guardada para @{args.login}. Importá las cookies primero:\n"
            f"  source ./setup.sh {args.profile} --chrome",
            file=sys.stderr,
        )
        return 1
    print(f">> Sesión cargada para @{args.login}.", file=sys.stderr)

    try:
        profile = instaloader.Profile.from_username(L.context, args.profile)
    except Exception as e:
        print(f"Error resolviendo el perfil @{args.profile}: {e}", file=sys.stderr)
        return 1
    # No accedemos a profile.mediacount: dispara metadata vía el GraphQL roto.
    print(
        f">> @{profile.username} (id {profile.userid}) — descargando ...",
        file=sys.stderr,
    )

    count = errors = 0
    try:
        for post in iter_profile_posts_v1(L, profile):
            count += 1
            try:
                L.download_post(post, target=profile.username)
            except Exception as e:
                errors += 1
                print(f"   ! error en {post.shortcode}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Error listando posts vía /api/v1/feed/user/: {e}", file=sys.stderr)
        if count == 0:
            return 1

    print(f">> Listo: {count} posts procesados ({errors} con error).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
