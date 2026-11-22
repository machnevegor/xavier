#!/bin/sh
# Start the display, the wallpaper, the window manager, the dock and two VNC servers on
# 127.0.0.1: 5900 is view-only, 5901 accepts input. computerd and Chromium are started later by
# `docker exec`. On SIGTERM computerd closes Chromium so that the profile is flushed before the
# container stops.
set -eu

Xvfb :0 -screen 0 1280x800x24 -nolisten tcp &
until [ -e /tmp/.X11-unix/X0 ]; do sleep 0.05; done
hsetroot -cover /opt/xavier/wallpaper.png
openbox &
tint2 -c /opt/xavier/tint2rc &
x11vnc -display :0 -localhost -rfbport 5900 -viewonly -shared -forever -nopw -quiet -bg
x11vnc -display :0 -localhost -rfbport 5901 -shared -forever -nopw -quiet -bg

stop() {
    pkill -TERM -f /opt/xavier/computerd.py || true
    while pgrep -f /opt/xavier/computerd.py >/dev/null; do sleep 0.1; done
    exit 0
}
trap stop TERM INT
while :; do sleep 1 & wait $!; done
