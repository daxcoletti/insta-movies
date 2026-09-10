#!/usr/bin/env bash
#
# notify_ok.sh — notificación de escritorio cuando un cron termina OK sin
# posts nuevos ("sin novedades"). Uso: notify_ok.sh "<título>" "<mensaje>"
#
# Mismo mecanismo que notify_fail.sh / notify_new.sh: cron corre sin entorno
# gráfico, así que apuntamos al bus de sesión del usuario. Urgencia critical
# para que persista hasta cerrarla (el cron corre a las 23:30 y quizás la veas
# recién a la mañana); el ícono de "tilde" la distingue de novedades y fallas.

export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
export DISPLAY="${DISPLAY:-:0}"

notify-send -u critical -a insta-movies -i emblem-default "$1" "$2" 2>/dev/null || true
