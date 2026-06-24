#!/usr/bin/env python3
"""
import_cookies.py — importa la sesión de Instagram desde un navegador y la guarda
como sesión reutilizable de instaloader (evita el prompt de contraseña).

Requisito: estar logueado en instagram.com en ese navegador.

Uso:
    python import_cookies.py [BROWSER] [USUARIO_IG]
      BROWSER     chrome (default) | firefox | chromium | brave | edge | opera
      USUARIO_IG  (opcional) tu usuario de IG; se usa si la verificación online
                  no puede determinarlo (p. ej. Instagram devuelve 401/throttle).

Mensajes informativos van a stderr; en stdout imprime SOLO el usuario detectado,
para que setup.sh lo capture.

Salida:
    0  sesión importada y guardada
    1  no hay sesión utilizable (sin login en el navegador, o sin poder resolver
       el usuario y sin fallback)
"""

import contextlib
import glob
import os
import sys

import instaloader

# Navegadores chromium-based: carpetas de config en Linux donde viven los perfiles,
# cada uno con su propio archivo de Cookies. browser_cookie3 lee el perfil "Default"
# por defecto, pero muchas instalaciones usan perfiles con nombre ("Profile 6", etc.),
# así que recorremos todos y elegimos el que tenga la cookie 'sessionid'.
_CHROMIUM_CONFIG_DIRS = {
    "chrome": ["~/.config/google-chrome"],
    "chromium": ["~/.config/chromium", "~/snap/chromium/common/chromium"],
    "brave": ["~/.config/BraveSoftware/Brave-Browser"],
    "edge": ["~/.config/microsoft-edge"],
    "opera": ["~/.config/opera"],
}


def _profile_cookie_files(browser: str) -> list:
    """Rutas de los archivos Cookies de cada perfil del navegador (chromium-based)."""
    files = []
    for base in _CHROMIUM_CONFIG_DIRS.get(browser, []):
        base = os.path.expanduser(base)
        for pat in ("*/Cookies", "*/Network/Cookies"):
            files.extend(glob.glob(os.path.join(base, pat)))
    return sorted(set(files))


def _load_instagram_cookies(loader, browser: str):
    """Devuelve (cookies, origen). Intenta el perfil por defecto y, si no hay
    'sessionid', recorre los demás perfiles del navegador."""
    # 1. Comportamiento por defecto de browser_cookie3 (perfil Default).
    try:
        cookies = loader(domain_name="instagram.com")
        if "sessionid" in {c.name for c in cookies}:
            return cookies, "perfil por defecto"
        default = cookies
    except Exception as e:
        print(f">> Aviso: no pude leer el perfil por defecto de {browser} ({e}).", file=sys.stderr)
        default = None

    # 2. Recorrer todos los perfiles buscando uno con sesión iniciada.
    for cf in _profile_cookie_files(browser):
        try:
            cookies = loader(cookie_file=cf, domain_name="instagram.com")
        except Exception as e:
            print(f">> Aviso: no pude leer {cf} ({e}).", file=sys.stderr)
            continue
        if "sessionid" in {c.name for c in cookies}:
            print(f">> Sesión encontrada en: {cf}", file=sys.stderr)
            return cookies, cf

    return default, None


def main() -> None:
    browser = (sys.argv[1] if len(sys.argv) > 1 else "chrome").lower()
    fallback_user = sys.argv[2] if len(sys.argv) > 2 else ""

    try:
        import browser_cookie3
    except ImportError:
        print("Falta browser_cookie3. Instalalo:  pip install browser_cookie3", file=sys.stderr)
        sys.exit(1)

    loader = getattr(browser_cookie3, browser, None)
    if loader is None:
        print(f"Navegador no soportado: {browser}", file=sys.stderr)
        print("Opciones: chrome, firefox, chromium, brave, edge, opera", file=sys.stderr)
        sys.exit(1)

    print(f">> Leyendo cookies de {browser} para instagram.com ...", file=sys.stderr)
    try:
        cookies, _ = _load_instagram_cookies(loader, browser)
    except Exception as e:  # DB bloqueada, keyring, etc.
        print(f"Error leyendo cookies de {browser}: {e}", file=sys.stderr)
        print(
            "Sugerencias: cerrá el navegador y reintentá; en Linux el llavero "
            "(keyring) debe estar desbloqueado.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Chequeo OFFLINE: ¿hay realmente una sesión iniciada en ese navegador?
    # Sin 'sessionid' no hay login posible, independientemente de Instagram.
    cookie_names = {c.name for c in cookies} if cookies else set()
    if "sessionid" not in cookie_names:
        print(
            f"No se encontró la cookie 'sessionid' de instagram.com en ningún perfil de {browser}.\n"
            "Eso significa que NO hay una sesión iniciada en ese navegador.\n"
            "Abrí instagram.com en ese navegador, iniciá sesión, y reintentá.",
            file=sys.stderr,
        )
        sys.exit(1)

    L = instaloader.Instaloader(max_connection_attempts=1)
    L.context._session.cookies.update(cookies)

    # Verificación ONLINE (best-effort): el endpoint de test_login usa un query_hash
    # viejo que a veces devuelve 401/throttle aunque la sesión sea válida. No la
    # tratamos como fatal: si falla, caemos al usuario pasado como argumento y dejamos
    # que la descarga real sea la prueba.
    username = None
    try:
        username = L.test_login()
    except Exception as e:
        print(f">> Aviso: no se pudo verificar online ({e}).", file=sys.stderr)

    if username:
        print(f">> Sesión verificada online para @{username}.", file=sys.stderr)
    elif fallback_user:
        username = fallback_user
        print(
            f">> No se pudo verificar online; uso el usuario indicado @{username}. "
            "La descarga dirá si la sesión sirve.",
            file=sys.stderr,
        )
    else:
        print(
            "Hay cookie 'sessionid' pero no pude resolver tu usuario "
            "(Instagram devolvió error en la verificación).\n"
            "Reintentá pasando tu usuario de IG:\n"
            "  source ./setup.sh <PERFIL> --chrome <TU_USUARIO_IG>",
            file=sys.stderr,
        )
        sys.exit(1)

    L.context.username = username
    # save_session_to_file() loguea "Saved session to ..." a stdout; lo redirigimos a
    # stderr para que stdout contenga SOLO el usuario (setup.sh captura stdout).
    with contextlib.redirect_stdout(sys.stderr):
        L.save_session_to_file()  # ~/.config/instaloader/session-<username>
    print(f">> Sesión guardada para @{username}.", file=sys.stderr)

    # stdout: solo el usuario, para que setup.sh lo capture.
    print(username)


if __name__ == "__main__":
    main()
