#!/usr/bin/env bash
#
# notify_blocked.sh — notificación de escritorio cuando Instagram no nos deja
# listar los posts (soft-block / rate-limit), no cuando algo se rompió.
# Uso: notify_blocked.sh "<título>" "<mensaje>"
#
# Se separa de notify_fail.sh a propósito: acá no hay nada que arreglar ni sesión
# que regenerar — el bloqueo se libera solo y cada reintento lo renueva. El ícono
# de advertencia lo distingue de una falla real (dialog-error) y de una corrida
# normal (emblem-default). Mismo mecanismo de siempre: cron corre sin entorno
# gráfico, así que apuntamos al bus de sesión del usuario.

export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
export DISPLAY="${DISPLAY:-:0}"

notify-send -u critical -a insta-movies -i dialog-warning "$1" "$2" 2>/dev/null || true
