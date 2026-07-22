#!/usr/bin/env bash
#
# notify_fail.sh — notificación de escritorio cuando un wrapper de cron falla.
# Uso: notify_fail.sh "<título>" "<mensaje>"
#
# Cron corre sin entorno gráfico: apuntamos al bus de sesión del usuario para
# que notify-send llegue al escritorio igual. Urgencia critical para que la
# notificación persista hasta cerrarla (el cron corre a las 23:30 y quizás la
# veas recién a la mañana).

export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
export DISPLAY="${DISPLAY:-:0}"

notify-send -u critical -a insta-movies -i dialog-error "$1" "$2" 2>/dev/null || true
