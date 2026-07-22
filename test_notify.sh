#!/usr/bin/env bash
#
# test_notify.sh — prueba manual de la notificación de escritorio que usan los
# wrappers de cron (via notify_fail.sh). Correlo y debería aparecer un popup.
# Si no aparece, revisá el diagnóstico que imprime (el sospechoso #1 es el
# modo "no molestar" del escritorio, que suprime los popups).

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ">> Diagnóstico:"
dnd_mate=$(gsettings get org.mate.NotificationDaemon do-not-disturb 2>/dev/null)
dnd_aya=$(gsettings get org.ayatana.indicator.notifications do-not-disturb 2>/dev/null)
echo "   do-not-disturb (MATE):    ${dnd_mate:-n/a}"
echo "   do-not-disturb (ayatana): ${dnd_aya:-n/a}"
if [ "$dnd_mate" = "true" ] || [ "$dnd_aya" = "true" ]; then
  echo "   ⚠ 'No molestar' está ACTIVADO: el popup no se va a mostrar."
  echo "     Desactivalo con:"
  echo "       gsettings set org.mate.NotificationDaemon do-not-disturb false"
  echo "       gsettings set org.ayatana.indicator.notifications do-not-disturb false"
fi

echo ">> Enviando notificación de prueba..."
"$HERE/notify_fail.sh" "insta-movies: prueba de notificación ($(date '+%H:%M:%S'))" \
  "Si ves esto, el aviso de fallas del cron funciona. Podés cerrarla."
echo ">> Enviada. Debería verse un popup (urgencia critical: queda hasta que la cierres)."
