#!/usr/bin/env bash
#
# notify_new.sh — notificación de escritorio cuando un cron detecta posts nuevos.
# Uso: notify_new.sh "<título>" "<mensaje>"
#
# Mismo mecanismo que notify_fail.sh: cron corre sin entorno gráfico, así que
# apuntamos al bus de sesión del usuario. Urgencia critical para que persista
# hasta cerrarla (el cron corre a las 23:30 y quizás la veas recién a la mañana);
# el ícono de información la distingue de una falla.

export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
export DISPLAY="${DISPLAY:-:0}"

notify-send -u critical -a insta-movies -i dialog-information "$1" "$2" 2>/dev/null || true
