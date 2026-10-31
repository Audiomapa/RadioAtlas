#!/bin/bash
# Instala el recorte del log y reinicia la pantalla (zona horaria nueva)
set -e
install -m 644 -o root -g root /home/qzzk/analisis_fsk/radioatlas-recortar-log /etc/cron.d/radioatlas-recortar-log
systemctl restart radioatlas-display-receptor.service
ls -la /etc/cron.d/radioatlas-recortar-log
systemctl is-active radioatlas-display-receptor.service
echo LISTO
