#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Applications/Enmotion 随手记.app"
PLIST="$HOME/Library/LaunchAgents/com.enmotion.quick-capture.plist"
mkdir -p "$APP/Contents/MacOS" "$HOME/Library/LaunchAgents" "$HOME/.config/enmotion-robot-sync/logs"
swiftc -swift-version 5 -O -framework AppKit -framework Carbon "$ROOT/macos/QuickCapture.swift" -o "$APP/Contents/MacOS/EnmotionCapture"
cp "$ROOT/macos/Info.plist" "$APP/Contents/Info.plist"
codesign --force --sign - "$APP"
python3 - "$APP" "$PLIST" <<'PY'
import pathlib, plistlib, sys
app, target = sys.argv[1:]
logs = pathlib.Path.home() / '.config/enmotion-robot-sync/logs'
data = {'Label': 'com.enmotion.quick-capture', 'ProgramArguments': [app + '/Contents/MacOS/EnmotionCapture'],
        'RunAtLoad': True, 'StandardOutPath': str(logs/'capture.log'), 'StandardErrorPath': str(logs/'capture-error.log')}
pathlib.Path(target).write_bytes(plistlib.dumps(data))
desktop = pathlib.Path.home()/'Desktop/Enmotion 随手记.app'
if not desktop.exists() and not desktop.is_symlink():
    desktop.symlink_to(app)
PY
launchctl bootout "gui/$(id -u)/com.enmotion.quick-capture" 2>/dev/null || true
for attempt in 1 2 3 4 5; do
    if ! launchctl print "gui/$(id -u)/com.enmotion.quick-capture" >/dev/null 2>&1; then break; fi
    sleep 1
done
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 1
open "$APP" --args --show
