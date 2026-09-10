#!/usr/bin/env bash
#
# run_daily_music.sh — wrapper para cron: actualiza la planilla de canciones de
# @owencutts (incremental), sincroniza la playlist de Spotify y sube la planilla a
# Google Drive. Pensado para correr una vez por día.
#
# Cron (ejemplo, todos los días 23:45):
#   45 23 * * * /home/dax/dev/insta-movies/run_daily_music.sh
#
# Toda la salida va a daily_music.log en esta carpeta.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1
cd "$HERE" || exit 1

# Loguear todo a daily_music.log (con rotación simple por tamaño: >5MB -> .1).
LOG="$HERE/daily_music.log"
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
python daily_music_update.py "${MUSIC_PROFILE:-owencutts}" 2>&1 | tee "$RUN_OUT"
rc="${PIPESTATUS[0]}"
echo ">> run_daily_music.sh terminó con código $rc"

NUEVOS="$(sed -n 's/^>> nuevo //p' "$RUN_OUT")"
RESUMEN="$(sed -n 's/^>> Planilla actualizada: //p' "$RUN_OUT" | tail -n1)"
rm -f "$RUN_OUT"
if [ -n "$NUEVOS" ]; then
  N="$(printf '%s\n' "$NUEVOS" | wc -l)"
  [ "$N" -eq 1 ] && QUE="1 tema nuevo" || QUE="$N temas nuevos"
  "$HERE/notify_new.sh" "insta-movies: $QUE de ${MUSIC_PROFILE:-owencutts}" "$NUEVOS"
fi

if [ "$rc" -ne 0 ]; then
  "$HERE/notify_fail.sh" "insta-movies: falló la actualización de música (código $rc)" \
    "Revisá daily_music.log. Si es error de login/sesión de Instagram: source ./setup.sh owencutts --chrome"
elif [ -z "$NUEVOS" ]; then
  "$HERE/notify_ok.sh" "insta-movies: música OK, sin posts nuevos" \
    "Corrida de ${MUSIC_PROFILE:-owencutts} terminó bien. Planilla: ${RESUMEN:-sin cambios}"
fi
exit "$rc"
