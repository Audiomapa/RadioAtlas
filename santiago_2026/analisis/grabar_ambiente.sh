#!/bin/bash
# Graba 5 min del ambiente de la galería desde el ZEDi8 (mismo micrófono y ganancia
# que usa el receptor). Para el receptor durante la grabación y lo vuelve a arrancar.
# Uso: sudo bash ~/analisis_fsk/grabar_ambiente.sh
SAL=/home/qzzk/analisis_fsk/galeria/ambiente_$(date +%Y%m%d_%H%M).wav
systemctl stop radioatlas-receptor.service
trap "systemctl start radioatlas-receptor.service; echo receptor reiniciado: \$(systemctl is-active radioatlas-receptor.service)" EXIT
echo "grabando 5 minutos en $SAL ..."
arecord -q -D hw:2,0 -f S32_LE -r 48000 -c 2 --duration=300 "$SAL"
chown qzzk:qzzk "$SAL"
ls -la "$SAL"
