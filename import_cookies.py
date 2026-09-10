#!/usr/bin/env python3
"""
import_cookies.py — importa la sesión de Instagram desde un navegador y la guarda
como sesión reutilizable de instaloader (evita el prompt de contraseña).

Requisito: estar logueado en instagram.com en ese navegador.

Uso:
    python import_cookies.py [BROWSER] [USUARIO_IG]
      BROWSER     chrome (default) | firefox | chromium | brave | edge | opera
      USUARIO_IG  (opcional) usuario de IG ESPERADO: si el navegador tiene varias
                  cuentas logueadas (una por perfil de Chrome), solo se acepta la
                  cookie que pertenece a este usuario; las demás se ignoran.
                  Sin este argumento se usa la primera sesión encontrada.

Mensajes informativos van a stderr; en stdout imprime SOLO el usuario detectado,
para que setup.sh lo capture.

Salida:
    0  sesión importada y guardada
    1  no hay sesión utilizable (sin login en el navegador, ninguna cookie del
       usuario esperado, o sin poder resolver el usuario)
"""

import contextlib
import glob
import os
import sys

import instaloader
import requests

from ig_feed import HEADERS  # mismos headers que el resto del pipeline

# Navegadores chromium-based: carpetas de config en Linux donde viven los perfiles,
# cada uno con su propio archivo de Cookies. browser_cookie3 lee el perfil "Default"
# por defecto, pero muchas instalaciones usan perfiles con nombre ("Profile 6", etc.),
# así que recorremos todos y verificamos a qué cuenta pertenece cada sesión.
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


def _ds_user_id(cookies) -> str:
    """ID numérico de la cuenta dueña de estas cookies (offline)."""
    for c in cookies:
        if c.name == "ds_user_id" and c.value:
            return c.value
    # Fallback: el sessionid empieza con "<userid>%3A..." (o "<userid>:...").
    for c in cookies:
        if c.name == "sessionid" and c.value:
            for sep in ("%3A", ":"):
                if sep in c.value:
                    return c.value.split(sep, 1)[0]
    return ""


def _session_username(cookies) -> str:
    """Resuelve online el usuario de IG dueño de estas cookies ("" si no se pudo,
    p. ej. rate-limit o sesión vencida)."""
    uid = _ds_user_id(cookies)
    if not uid:
        return ""
    sess = requests.Session()
    sess.cookies.update(cookies)
    try:
        r = sess.get(
            f"https://www.instagram.com/api/v1/users/{uid}/info/",
            headers={**HEADERS, "Referer": "https://www.instagram.com/"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["user"]["username"]
    except Exception as e:
        print(f">> Aviso: no pude verificar el dueño de una cookie ({e}).", file=sys.stderr)
        return ""


def _cookie_candidates(loader, browser: str) -> list:
    """Lista de (cookies, origen) con cookie 'sessionid', una por cuenta de IG
    (dedup por ds_user_id): el perfil por defecto + todos los perfiles del navegador."""
    candidates = []
    seen_ids = set()

    def add(cookies, origin):
        if "sessionid" not in {c.name for c in cookies}:
            return
        uid = _ds_user_id(cookies)
        if uid in seen_ids:
            return
        seen_ids.add(uid)
        candidates.append((cookies, origin))

    try:
        add(loader(domain_name="instagram.com"), "perfil por defecto")
    except Exception as e:
        print(f">> Aviso: no pude leer el perfil por defecto de {browser} ({e}).", file=sys.stderr)

    for cf in _profile_cookie_files(browser):
        try:
            add(loader(cookie_file=cf, domain_name="instagram.com"), cf)
        except Exception as e:
            print(f">> Aviso: no pude leer {cf} ({e}).", file=sys.stderr)

    return candidates


def main() -> None:
    browser = (sys.argv[1] if len(sys.argv) > 1 else "chrome").lower()
    expected_user = (sys.argv[2] if len(sys.argv) > 2 else "").strip().lstrip("@")

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
        candidates = _cookie_candidates(loader, browser)
    except Exception as e:  # DB bloqueada, keyring, etc.
        print(f"Error leyendo cookies de {browser}: {e}", file=sys.stderr)
        print(
            "Sugerencias: cerrá el navegador y reintentá; en Linux el llavero "
            "(keyring) debe estar desbloqueado.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Chequeo OFFLINE: ¿hay realmente alguna sesión iniciada en ese navegador?
    if not candidates:
        print(
            f"No se encontró la cookie 'sessionid' de instagram.com en ningún perfil de {browser}.\n"
            "Eso significa que NO hay una sesión iniciada en ese navegador.\n"
            "Abrí instagram.com en ese navegador, iniciá sesión, y reintentá.",
            file=sys.stderr,
        )
        sys.exit(1)

    selected = None
    username = ""
    if expected_user:
        # Solo aceptamos la cookie que pertenece al usuario esperado: puede haber
        # varias cuentas de IG logueadas (una por perfil de Chrome).
        unverified = 0
        found_users = []
        for cookies, origin in candidates:
            owner = _session_username(cookies)
            if not owner:
                unverified += 1
                continue
            if owner.lower() == expected_user.lower():
                print(f">> Sesión de @{owner} encontrada ({origin}).", file=sys.stderr)
                selected, username = cookies, owner
                break
            found_users.append(owner)
            print(f">> Ignoro la sesión de @{owner} ({origin}): no es @{expected_user}.", file=sys.stderr)

        if selected is None and len(candidates) == 1 and unverified:
            # Única sesión y no se pudo verificar online (throttle/rate-limit):
            # la usamos asumiendo que es la esperada; la descarga real dirá si sirve.
            selected, username = candidates[0][0], expected_user
            print(
                f">> No pude verificar la única sesión encontrada; asumo que es @{expected_user}. "
                "La descarga dirá si la sesión sirve.",
                file=sys.stderr,
            )
        if selected is None:
            print(
                f"Ninguna cookie de {browser} pertenece a @{expected_user}."
                + (f" Cuentas encontradas: {', '.join('@' + u for u in found_users)}." if found_users else "")
                + (f" ({unverified} sesión/es sin poder verificar — ¿rate-limit? Esperá unos minutos y reintentá.)"
                   if unverified else "")
                + f"\nIniciá sesión como @{expected_user} en instagram.com en algún perfil de {browser} y reintentá.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        # Sin usuario esperado: comportamiento histórico, primera sesión encontrada.
        selected, origin = candidates[0]
        print(f">> Sin usuario esperado: uso la primera sesión encontrada ({origin}).", file=sys.stderr)
        username = _session_username(selected)

    L = instaloader.Instaloader(max_connection_attempts=1)
    L.context._session.cookies.update(selected)

    if not username:
        # Best-effort: test_login() usa un query_hash viejo que a veces devuelve
        # 401/throttle aunque la sesión sea válida; no lo tratamos como fatal.
        try:
            username = L.test_login()
        except Exception as e:
            print(f">> Aviso: no se pudo verificar online ({e}).", file=sys.stderr)
    if not username:
        print(
            "Hay cookie 'sessionid' pero no pude resolver tu usuario "
            "(Instagram devolvió error en la verificación).\n"
            "Reintentá pasando tu usuario de IG:\n"
            "  source ./setup.sh <PERFIL> --chrome <TU_USUARIO_IG>",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f">> Sesión verificada para @{username}.", file=sys.stderr)
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
