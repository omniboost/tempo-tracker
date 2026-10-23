#!/bin/bash
# Installeert Tempo Tracker voor de huidige gebruiker en start hem automatisch bij inloggen.
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
LABEL="io.omniboost.tempo-tracker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/TempoTracker.log"

PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10) or sys.version_info >= (3, 15))' 2>/dev/null; then
        PYTHON="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "Python 3.10 to 3.14 required. Install it with e.g.: brew install python@3.13" >&2
    exit 1
fi
echo "Python: $PYTHON"

"$PYTHON" -m venv .venv
# Expliciet publieke PyPI: een (verlopen) privé-index in je pip-config breekt de installatie anders.
.venv/bin/pip install --quiet --upgrade --index-url https://pypi.org/simple pip
.venv/bin/pip install --quiet --index-url https://pypi.org/simple -r requirements.txt

mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$LOG")"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$ROOT/.venv/bin/python</string>
        <string>-m</string>
        <string>tempo_tracker</string>
    </array>
    <key>WorkingDirectory</key><string>$ROOT</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>LimitLoadToSessionType</key><string>Aqua</string>
    <key>ProcessType</key><string>Interactive</string>
    <key>StandardOutPath</key><string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
# launchd kan de eerste start uitstellen; nu meteen starten.
launchctl kickstart "gui/$(id -u)/$LABEL"
echo "Installed. Tempo Tracker is now in your menu bar (⏱) and starts when you log in."
