#!/bin/bash
# start_display_receptor.sh — lanzador del display del receptor
# Idéntico al del emisor. Ajustar xrandr si la pantalla es landscape.
export PYTHONUNBUFFERED=1
export SDL_AUDIODRIVER=dummy
export HOME=/home/qzzk
xrandr --output HDMI-1 --rotate right
python3 -u /home/qzzk/display_receptor.py >> /tmp/radioatlas_receptor.log 2>&1
