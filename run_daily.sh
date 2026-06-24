#!/usr/bin/env bash
#
# run_daily.sh — wrapper para cron: actualiza la planilla de películas (incremental)
# y la sube a Google Drive. Pensado para correr una vez por día.
#
# Cron (ejemplo, todos los días 23:30):
#   30 23 * * * /home/dax/dev/insta-movies/run_daily.sh
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

python daily_update.py "${PROFILE:-juan.amonda}"
rc=$?
echo ">> run_daily.sh terminó con código $rc"
exit "$rc"
