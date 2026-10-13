#!/bin/bash
# Remove Bea from this Mac. Keeps backend/.env and the conversation database unless you pass --all.
set -uo pipefail
LABEL="in.betteranalytics.pom-backend"
pkill -x Bea 2>/dev/null; pkill -x Pom 2>/dev/null
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
for d in /Applications "$HOME/Applications"; do for a in Bea Pom; do [[ -d "$d/$a.app" ]] && rm -rf "$d/$a.app" && echo "Removed $d/$a.app"; done; done
if [[ "${1:-}" == "--all" ]]; then
  rm -rf "$HOME/Library/Application Support/Bea" "$HOME/Library/Application Support/Pom" "$HOME/.pom-server" "$HOME/Library/Logs/Bea" "$HOME/Library/Logs/Pom"
  echo "Removed settings, conversations and logs."
fi
echo "Bea has been uninstalled."
