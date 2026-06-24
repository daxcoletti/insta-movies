#!/usr/bin/env python3
"""
import_sessionid.py — construye una sesión de instaloader a partir de tu cookie
`sessionid` de Instagram (la que ya tiene tu navegador logueado).

Útil cuando el navegador logueado está en OTRA máquina que la que corre los
scripts: copiás el valor de la cookie y lo traés acá.

Cómo obtener la cookie (en el navegador donde SÍ estás logueado en Instagram):
  DevTools (F12) → Application/Almacenamiento → Cookies → https://www.instagram.com
  → copiá el valor de `sessionid` (y opcionalmente `ds_user_id` y `csrftoken`).

Uso (la cookie va por variable de entorno, no por argumento, para no dejarla en
el historial de la shell):
    export IG_SESSIONID='...'            # obligatorio
    export IG_DS_USER_ID='...'           # opcional
    export IG_CSRFTOKEN='...'            # opcional
    python import_sessionid.py <TU_USUARIO_IG>

stdout: solo el usuario (para que setup.sh lo capture). Mensajes → stderr.
"""

import os
import sys

import instaloader


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Error: falta tu usuario de Instagram.", file=sys.stderr)
        print("Uso: IG_SESSIONID=... python import_sessionid.py <TU_USUARIO_IG>", file=sys.stderr)
        sys.exit(1)
    username = sys.argv[1].strip()

    sessionid = os.environ.get("IG_SESSIONID", "").strip()
    if not sessionid:
        print(
            "Error: la variable IG_SESSIONID no está seteada.\n"
            "Copiá la cookie 'sessionid' de instagram.com desde tu navegador logueado y:\n"
            "  export IG_SESSIONID='...'",
            file=sys.stderr,
        )
        sys.exit(1)

    L = instaloader.Instaloader(max_connection_attempts=1)
    jar = L.context._session.cookies
    jar.set("sessionid", sessionid, domain=".instagram.com")
    ds_user_id = os.environ.get("IG_DS_USER_ID", "").strip()
    if ds_user_id:
        jar.set("ds_user_id", ds_user_id, domain=".instagram.com")
    csrftoken = os.environ.get("IG_CSRFTOKEN", "").strip()
    if csrftoken:
        jar.set("csrftoken", csrftoken, domain=".instagram.com")

    # Verificación best-effort (el endpoint puede dar 401/throttle aun con sesión válida).
    try:
        verified = L.test_login()
    except Exception as e:
        verified = None
        print(f">> Aviso: no se pudo verificar online ({e}).", file=sys.stderr)

    if verified and verified != username:
        print(
            f">> Aviso: la cookie corresponde a @{verified}, no a @{username}. "
            f"Uso @{verified}.",
            file=sys.stderr,
        )
        username = verified

    L.context.username = username
    L.save_session_to_file()  # ~/.config/instaloader/session-<username>
    print(f">> Sesión guardada para @{username}. La descarga dirá si la sesión sirve.", file=sys.stderr)

    print(username)


if __name__ == "__main__":
    main()
