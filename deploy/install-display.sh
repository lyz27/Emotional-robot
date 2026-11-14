#!/bin/bash
# Run with sudo on the robot. Enables desktop auto-login for the specified user.
set -euo pipefail
DISPLAY_USER="${1:-lyz}"
DISPLAY_HOME="$(getent passwd "$DISPLAY_USER" | cut -d: -f6)"
test -n "$DISPLAY_HOME"
test -x "$DISPLAY_HOME/.local/opt/firefox/firefox" || test -x /usr/bin/chromium-browser
mkdir -p "$DISPLAY_HOME/.local/bin" "$DISPLAY_HOME/.config/autostart"
cat > "$DISPLAY_HOME/.local/bin/enmotion-display" <<'SH'
#!/bin/bash
export DISPLAY="${DISPLAY:-:0}"
xset s off 2>/dev/null || true
xset -dpms 2>/dev/null || true
xset s noblank 2>/dev/null || true
gsettings set org.gnome.desktop.session idle-delay 0 || true
gsettings set org.gnome.desktop.screensaver lock-enabled false || true
until curl -fsS http://127.0.0.1:8080/face >/dev/null; do sleep 2; done
while true; do
 if test -x "$HOME/.local/opt/firefox/firefox"; then
  mkdir -p "$HOME/.config/enmotion-display/firefox"
  cat > "$HOME/.config/enmotion-display/firefox/user.js" <<'PREFS'
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.sessionstore.resume_from_crash", false);
PREFS
  "$HOME/.local/opt/firefox/firefox" --no-remote --profile "$HOME/.config/enmotion-display/firefox" --kiosk http://127.0.0.1:8080/face
 else
  /usr/bin/chromium-browser --kiosk --no-first-run --no-default-browser-check --autoplay-policy=no-user-gesture-required --user-data-dir="$HOME/snap/chromium/common/enmotion-display" http://127.0.0.1:8080/face
 fi
 sleep 5
done
SH
chmod +x "$DISPLAY_HOME/.local/bin/enmotion-display"
cat > "$DISPLAY_HOME/.config/autostart/enmotion-display.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Enmotion Display
Exec=$DISPLAY_HOME/.local/bin/enmotion-display
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
Terminal=false
EOF
chown "$DISPLAY_USER:$DISPLAY_USER" "$DISPLAY_HOME/.local/bin/enmotion-display" "$DISPLAY_HOME/.config/autostart/enmotion-display.desktop"
cp -n /etc/gdm3/custom.conf /etc/gdm3/custom.conf.before-enmotion
python3 - "$DISPLAY_USER" <<'PY'
import configparser,sys
p='/etc/gdm3/custom.conf'
c=configparser.ConfigParser(); c.optionxform=str; c.read(p)
c['daemon']['AutomaticLoginEnable']='true'
c['daemon']['AutomaticLogin']=sys.argv[1]
c['daemon']['WaylandEnable']='false'
with open(p,'w') as f:c.write(f)
PY
systemctl enable gdm3 enmotion-runtime
systemctl restart enmotion-runtime
echo 'Installed display autostart. Restart gdm3 or reboot to activate desktop auto-login.'
