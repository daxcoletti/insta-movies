#!/usr/bin/env python3
"""
spotify_auth.py — autorización inicial de Spotify (correr UNA vez, en una máquina con
navegador). Abre el navegador, te pide autorizar la app y guarda el token en
.spotify_cache. Después el cron (daily_music_update.py) reusa ese token y lo refresca
solo, sin volver a abrir el navegador.

Uso:
    # con las SPOTIPY_* ya en el entorno (cargá el .env):
    python spotify_auth.py
"""

import sys

from spotify_sync import get_spotify


def main() -> int:
    try:
        sp = get_spotify(open_browser=True)
        me = sp.me()
    except Exception as e:
        print(f"Error autorizando Spotify: {e}", file=sys.stderr)
        return 1
    print(f">> Autorizado como: {me.get('display_name')} ({me.get('id')})")
    print(">> Token guardado en .spotify_cache. Ya podés correr el pipeline / el cron.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
