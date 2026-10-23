#!/bin/bash
# Verwijdert de LaunchAgent en de tokens uit de sleutelhanger. Laat je lokale database staan.
set -euo pipefail

LABEL="io.omniboost.tempo-tracker"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
security delete-generic-password -s tempo-tracker -a jira-api-token >/dev/null 2>&1 || true
security delete-generic-password -s tempo-tracker -a tempo-api-token >/dev/null 2>&1 || true
echo "Removed. Your tracked time is still in ~/Library/Application Support/TempoTracker (delete that folder manually if you no longer need it)."
