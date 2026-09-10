#!/usr/bin/env bash
#
# setup.sh — prepara el entorno y descarga los posts públicos de un perfil de Instagram.
#
# Uso:
#   source ./setup.sh <PERFIL> [USUARIO_LOGIN]
#
#   <PERFIL>   perfil de Instagram a descargar.
#   [AUTH]     cómo autenticarse (Instagram bloquea la descarga anónima con 403):
#                --chrome | --firefox | --chromium | --brave | --edge | --opera [USUARIO_IG]
#                    importa la sesión ya iniciada en ESE navegador, en ESTA máquina.
#                    (Solo sirve si corrés esto donde tenés el navegador logueado.)
#                    USUARIO_IG = cuenta esperada: si el navegador tiene varias
#                    cuentas logueadas (una por perfil de Chrome), solo se usa la
#                    cookie de ESA cuenta. Si no lo pasás, se toma IG_LOGIN_USER
#                    de .env (p. ej. daxcoletti).
#                --sessionid <USUARIO_IG>   (con IG_SESSIONID en el entorno)
#                    construye la sesión con tu cookie sessionid — útil si el
#                    navegador logueado está en otra máquina. Ej:
#                    IG_SESSIONID='...' source ./setup.sh <PERFIL> --sessionid <USUARIO_IG>
#                <USUARIO_IG>
#                    login por usuario/contraseña (instaloader pide la contraseña;
#                    requiere una terminal interactiva).
#                (vacío) descarga anónima — probablemente falle con 403.
#
# Crea un virtualenv en venv/, instala instaloader + anthropic y descarga los posts
# (solo imágenes) del perfil indicado en la carpeta {profile}/.
#
# Recomendado correrlo con "source" para que el venv quede activo en tu shell.

# ¿Estamos siendo "sourced" o ejecutados? (define cómo terminamos)
if (return 0 2>/dev/null); then _SOURCED=1; else _SOURCED=0; fi

_insta_setup_main() {
  # Sin "set -e": al hacer source, un errexit cerraría tu shell. Manejamos los
  # errores explícitamente y devolvemos un código de salida.
  set -uo pipefail

  local PROFILE="${1:-}"
  local AUTH="${2:-}"
  local IG_USER_HINT="${3:-}"   # opcional: usuario de IG esperado (modo navegador/sessionid)

  if [ -z "$PROFILE" ]; then
    echo "Error: falta el perfil de Instagram." >&2
    echo "Uso: source ./setup.sh <PERFIL> [--chrome | --firefox | <USUARIO_IG>]" >&2
    return 1
  fi

  local HERE
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || return 1
  cd "$HERE" || return 1

  # 1. Crear el virtualenv si no existe.
  if [ ! -d venv ]; then
    echo ">> Creando virtualenv en venv/ ..."
    python3 -m venv venv || { echo "Error: no se pudo crear el venv." >&2; return 1; }
  fi

  # 2. Activar el venv e instalar dependencias.
  # shellcheck disable=SC1091
  source venv/bin/activate || { echo "Error: no se pudo activar el venv." >&2; return 1; }
  echo ">> Instalando dependencias (instaloader, anthropic) ..."
  pip install --upgrade pip >/dev/null || { echo "Error: fallo al actualizar pip." >&2; return 1; }
  pip install -r requirements.txt || { echo "Error: fallo al instalar dependencias." >&2; return 1; }

  # 3. Armar argumentos de autenticación.
  local login_args=()
  local browser=""
  local ig_user=""
  case "$AUTH" in
    --chrome|chrome)       browser="chrome" ;;
    --firefox|firefox)     browser="firefox" ;;
    --chromium|chromium)   browser="chromium" ;;
    --brave|brave)         browser="brave" ;;
    --edge|edge)           browser="edge" ;;
    --opera|opera)         browser="opera" ;;
  esac

  if [ "$AUTH" = "--sessionid" ] || [ "$AUTH" = "sessionid" ]; then
    # Modo cookie manual: sesión a partir de IG_SESSIONID (navegador en otra máquina).
    if [ -z "$IG_USER_HINT" ]; then
      echo "Error: en modo --sessionid pasá tu usuario de IG." >&2
      echo "Uso: IG_SESSIONID='...' source ./setup.sh ${PROFILE} --sessionid <TU_USUARIO_IG>" >&2
      return 1
    fi
    echo ">> Construyendo sesión desde IG_SESSIONID para @${IG_USER_HINT} ..."
    ig_user="$(python import_sessionid.py "$IG_USER_HINT")" || {
      echo "Error: no se pudo construir la sesión desde IG_SESSIONID." >&2
      return 1
    }
    echo ">> Usaré la sesión de @${ig_user}."
    login_args=(--login="$ig_user")
  elif [ -n "$browser" ]; then
    # Modo cookies: importar la sesión del navegador y bajar con ese usuario.
    # El navegador puede tener varias cuentas de IG logueadas (una por perfil de
    # Chrome); import_cookies.py solo acepta la cookie del usuario esperado.
    # Si no vino por argumento, lo tomamos de IG_LOGIN_USER en .env.
    local expected_user="$IG_USER_HINT"
    if [ -z "$expected_user" ] && [ -f .env ]; then
      expected_user="$(sed -n 's/^IG_LOGIN_USER=//p' .env | tail -n1)"
      [ -n "$expected_user" ] && echo ">> Usuario de IG esperado (IG_LOGIN_USER de .env): @${expected_user}"
    fi
    echo ">> Importando sesión de Instagram desde ${browser} ..."
    ig_user="$(python import_cookies.py "$browser" "$expected_user")" || {
      echo "Error: no se pudo importar la sesión desde ${browser}." >&2
      return 1
    }
    echo ">> Usaré la sesión de @${ig_user}."
    login_args=(--login="$ig_user")
  elif [ -n "$AUTH" ]; then
    # Modo usuario/contraseña (requiere terminal interactiva).
    echo ">> Iniciando sesión en Instagram como @${AUTH}"
    echo "   (instaloader pedirá tu contraseña la primera vez y guardará la sesión)."
    login_args=(--login="$AUTH")
    ig_user="$AUTH"
  else
    echo ">> Aviso: sin autenticación."
    echo "   Instagram suele rechazar la descarga anónima con 403 Forbidden."
    echo "   Reintentá importando las cookies del navegador:"
    echo "     source ./setup.sh ${PROFILE} --chrome"
  fi

  # 4. Descargar los posts del perfil.
  echo ">> Descargando posts de @${PROFILE} ..."
  if instaloader \
        --no-videos \
        --no-video-thumbnails \
        --no-metadata-json \
        --dirname-pattern="{profile}" \
        "${login_args[@]}" \
        "$PROFILE"; then
    :  # ok por la vía normal (GraphQL)
  elif [ -n "$ig_user" ]; then
    # El listado por GraphQL de instaloader puede fallar con
    # "400 Bad Request - invalid request" porque Instagram deprecó el doc_id de la
    # timeline. Si tenemos sesión, caemos al endpoint /api/v1/feed/user/ vía
    # download_posts.py (baja el thumbnail de cada Reel + el caption).
    echo "" >&2
    echo ">> El listado por GraphQL falló; reintento vía /api/v1/feed/user/ ..." >&2
    if ! python download_posts.py "$PROFILE" --login "$ig_user"; then
      echo "Error: la descarga falló (también el fallback /api/v1/feed/user/)." >&2
      return 1
    fi
  else
    echo "" >&2
    echo "Error: la descarga falló." >&2
    echo "Causas frecuentes: perfil inexistente o privado, bloqueo anónimo (403)," >&2
    echo "o sesión no válida. Probá importando las cookies del navegador:" >&2
    echo "  source ./setup.sh ${PROFILE} --chrome" >&2
    return 1
  fi

  # 5. Indicar el siguiente paso (solo si todo salió bien).
  cat <<EOF

==================================================================
 Listo. El virtualenv está activado y los posts están en ./${PROFILE}/

 Siguiente paso — extraer los títulos de películas:

   export ANTHROPIC_API_KEY=sk-ant-...        # tu API key de Anthropic
   python extract_movies.py ${PROFILE}

 Opcional — planilla (fecha, película, año, link) por post:

   python build_spreadsheet.py ${PROFILE} --login <TU_USUARIO_IG>

 (Si ejecutaste este script con "./setup.sh" en vez de "source",
  reactivá el venv en tu shell con:  source venv/bin/activate)
==================================================================
EOF
}

_insta_setup_main "$@"
_rc=$?
unset -f _insta_setup_main
if [ "$_SOURCED" = 1 ]; then
  unset _SOURCED
  return "$_rc"
else
  exit "$_rc"
fi
