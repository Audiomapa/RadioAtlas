#!/bin/bash
export PYTHONUNBUFFERED=1
export SDL_AUDIODRIVER=dummy
export HOME=/home/qzzk
xrandr --output HDMI-1 --rotate right
python3 -u /home/qzzk/rio_sstv/display_emisor.py >> /tmp/radioatlas.log 2>&1
