#!/usr/bin/env bash
#
# run_daily.sh — wrapper para cron: actualiza la planilla de películas (incremental)
# y la sube a Google Drive.
#
# Cron (cada 3 días, 23:30):
#   30 23 */3 * * /home/dax/dev/insta-movies/run_daily.sh
#
# Cada 3 días y no a diario: el perfil no publica todos los días y espaciar las
# corridas baja la chance del soft-block de Instagram. Como la actualización es
# incremental, una corrida recupera todo lo publicado desde la anterior.
#
# Códigos de salida: 0 OK · 2 Instagram bloqueó el listado (no es una falla) ·
# otro = error real.
#
# Toda la salida va a daily.log en esta carpeta.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1
cd "$HERE" || exit 1

# Loguear todo a daily.log (con rotación simple por tamaño: >5MB -> .1).
LOG="$HERE/daily.log"
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
  mv -f "$LOG" "$LOG.1"
fi
exec >>"$LOG" 2>&1

echo "===================== $(date '+%Y-%m-%d %H:%M:%S %z') ====================="

# Cargar variables (.env) y activar el venv.
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "Error: falta ANTHROPIC_API_KEY (¿está .env?)."; exit 1
fi
# shellcheck disable=SC1091
source venv/bin/activate || { echo "Error: no pude activar venv."; exit 1; }

# Capturamos la salida de esta corrida (además del log) para poder notificar
# los posts nuevos detectados (líneas ">> nuevo ...").
RUN_OUT="$(mktemp)" || exit 1
python daily_update.py "${PROFILE:-juan.amonda}" 2>&1 | tee "$RUN_OUT"
rc="${PIPESTATUS[0]}"
echo ">> run_daily.sh terminó con código $rc"

NUEVOS="$(sed -n 's/^>> nuevo //p' "$RUN_OUT")"
RESUMEN="$(sed -n 's/^>> Planilla actualizada: //p' "$RUN_OUT" | tail -n1)"
rm -f "$RUN_OUT"
if [ -n "$NUEVOS" ]; then
  N="$(printf '%s\n' "$NUEVOS" | wc -l)"
  [ "$N" -eq 1 ] && QUE="1 película nueva" || QUE="$N películas nuevas"
  "$HERE/notify_new.sh" "insta-movies: $QUE de ${PROFILE:-juan.amonda}" "$NUEVOS"
fi

# Código 2 = Instagram no nos deja listar los posts (soft-block / rate-limit).
# No es una falla del pipeline: no hay nada que arreglar y la sesión está sana,
# así que se avisa distinto y sin instrucciones de regenerar nada.
if [ "$rc" -eq 2 ]; then
  "$HERE/notify_blocked.sh" "insta-movies: Instagram bloqueó el listado de películas" \
    "$(printf 'La planilla quedó como estaba. El bloqueo se libera solo; la próxima corrida reintenta.\n\nMientras tanto podés agregar los que falten a mano:\n  python add_posts.py <link> ...')"
elif [ "$rc" -ne 0 ]; then
  "$HERE/notify_fail.sh" "insta-movies: falló la actualización de películas (código $rc)" \
    "Revisá daily.log. Si es error de login/sesión de Instagram: source ./setup.sh juan.amonda --chrome"
elif [ -z "$NUEVOS" ]; then
  "$HERE/notify_ok.sh" "insta-movies: películas OK, sin posts nuevos" \
    "Corrida de ${PROFILE:-juan.amonda} terminó bien. Planilla: ${RESUMEN:-sin cambios}"
fi
exit "$rc"
