#!/bin/bash
# Run as the robot desktop user, never root.
set -euo pipefail
if [[ "${1:-}" == --install ]]; then
 mkdir -p "$HOME/.local/bin" "$HOME/.config/autostart"
 cp "$0" "$HOME/.local/bin/enmotion-ugreen"
 chmod +x "$HOME/.local/bin/enmotion-ugreen"
 cat > "$HOME/.config/autostart/enmotion-ugreen.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Enmotion UGREEN Audio
Exec=$HOME/.local/bin/enmotion-ugreen
X-GNOME-Autostart-enabled=true
Terminal=false
EOF
 exit 0
fi
for attempt in {1..30}; do
 sink=$(pactl list short sinks | awk '/UGREEN_Camera_Audio/ {print $2; exit}')
 source=$(pactl list short sources | awk '/UGREEN_Camera_Audio/ && !/monitor/ {print $2; exit}')
 if [[ -n "$sink" && -n "$source" ]]; then
  pactl set-default-sink "$sink"
  pactl set-default-source "$source"
  pactl set-sink-mute "$sink" 0
  pactl set-source-mute "$source" 0
  pactl set-sink-volume "$sink" 80%
  # CM797 exposes a hardware PCM control whose low settings produce no audible output.
  amixer -c Audio sset PCM unmute >/dev/null
  exit 0
 fi
 sleep 1
done
echo 'UGREEN Camera Audio unavailable; retained existing defaults' >&2
exit 1
