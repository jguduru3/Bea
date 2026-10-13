#!/bin/bash
# One-command setup of Bea on a Mac:
#   1. backend in a Python virtualenv, with your API keys in backend/.env (Claude = brain, Sarvam = voice)
#   2. Bea's own browser (Chromium for Playwright) for web tasks
#   3. backend started now and at every login (LaunchAgent, local only: 127.0.0.1:8080)
#   4. Bea.app built for this Mac (with the offline "Hey Bea" wake word model), signed ad hoc, installed
#   5. the app connected to the backend automatically (no token to copy)
#
#   cd Bea_avatar && ./mac/install.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
LABEL="in.betteranalytics.pom-backend"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DATA="$HOME/.pom-server"
APPDATA="$HOME/Library/Application Support/Bea"
LOGS="$HOME/Library/Logs/Bea"
PORT=8080

say()  { printf "\n\033[1;34m▸\033[0m %s\n" "$*"; }
ok()   { printf "  \033[1;32m✓\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }
ask()  { local p="$1" d="${2:-}" v; read -r -p "  $p" v; echo "${v:-$d}"; }
secret() { local p="$1" v; read -r -s -p "  $p" v; echo >&2; echo "$v"; }
setenv() { # setenv KEY VALUE  (replace or append in .env)
  local k="$1" v="$2" f="$BACKEND/.env"
  if grep -q "^$k=" "$f"; then
    python3 - "$f" "$k" "$v" <<'PY'
import sys
f, k, v = sys.argv[1:]
lines = open(f).read().splitlines()
open(f, "w").write("\n".join(f"{k}={v}" if l.startswith(k + "=") else l for l in lines) + "\n")
PY
  else echo "$k=$v" >> "$f"; fi
}

[[ "$(uname)" == "Darwin" ]] || die "This installer is for macOS."
echo "Setting up Bea on $(scutil --get ComputerName 2>/dev/null || hostname) ($(uname -m))"

# ------------------------------------------------------------ prerequisites
say "Checking tools"
if ! command -v node >/dev/null && [[ -s "$HOME/.nvm/nvm.sh" ]]; then source "$HOME/.nvm/nvm.sh"; nvm use --lts >/dev/null 2>&1 || nvm install --lts; fi
command -v node >/dev/null || die "Node.js 20+ is needed: https://nodejs.org (or: brew install node)"
(( $(node -p 'process.versions.node.split(".")[0]') >= 20 )) || die "Node.js 20+ is needed (found $(node -v))."
ok "Node $(node -v)"

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then PY="$(command -v "$c")"; break; fi
done
[[ -n "$PY" ]] || die "Python 3.10+ is needed. Install it with: brew install python@3.12  (or from https://python.org)"
ok "Python $("$PY" -V | cut -d' ' -f2)"
xcode-select -p >/dev/null 2>&1 || die "Apple's command line tools are needed: run  xcode-select --install  then re-run this script."
ok "Xcode command line tools"

# ------------------------------------------------------------ backend
say "Setting up the backend"
"$PY" -m venv "$BACKEND/.venv"
"$BACKEND/.venv/bin/pip" install --quiet --upgrade pip
"$BACKEND/.venv/bin/pip" install --quiet -e "$BACKEND"
ok "Python packages installed"
"$BACKEND/.venv/bin/python" -m playwright install chromium >/dev/null
ok "Bea's browser installed (Chromium for Playwright)"

mkdir -p "$DATA" "$LOGS" "$APPDATA"
[[ -f "$BACKEND/.env" ]] || cp "$BACKEND/.env.example" "$BACKEND/.env"
chmod 600 "$BACKEND/.env"

current() { grep -E "^$1=" "$BACKEND/.env" | head -1 | cut -d= -f2- || true; }

# Claude is Bea's brain (chat and agent), OpenAI an optional backup for chat, Sarvam her voice.
# Keys can also be added later in the app: Settings > API keys.
setenv POM_LLM_PROVIDER anthropic
setenv POM_LLM_FALLBACK_PROVIDER openai
setenv POM_ALLOW_DEVICE_KEY_MANAGEMENT true
if [[ -z "$(current POM_ANTHROPIC_API_KEY)" ]]; then
  echo "  Bea's brain is Claude. Paste an Anthropic API key (starts with sk-ant-api), or press Enter to add it later in Settings."
  k=$(secret "Anthropic API key: "); [[ -n "$k" ]] && setenv POM_ANTHROPIC_API_KEY "$k"
fi
if [[ -z "$(current POM_OPENAI_API_KEY)" ]]; then
  k=$(secret "OpenAI API key for backup (Enter to skip): "); [[ -n "$k" ]] && setenv POM_OPENAI_API_KEY "$k"
fi
if [[ -z "$(current POM_SARVAM_API_KEY)" ]]; then
  k=$(secret "Sarvam AI API key for voice (Enter to skip): "); [[ -n "$k" ]] && setenv POM_SARVAM_API_KEY "$k"
fi

TOKEN="$(current POM_BOOTSTRAP_DEVICE_TOKEN)"
if [[ -z "$TOKEN" || "$TOKEN" == pom_replace* ]]; then
  TOKEN="pom_$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')"
  setenv POM_BOOTSTRAP_DEVICE_TOKEN "$TOKEN"
fi
ADMIN="$(current POM_ADMIN_API_KEY)"
if [[ -z "$ADMIN" || "$ADMIN" == change-me* ]]; then
  setenv POM_ADMIN_API_KEY "$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi
setenv POM_DATABASE_URL "sqlite+aiosqlite:///$DATA/pom.db"
setenv POM_DATA_DIR "$DATA/data"
setenv POM_SECRET_FILE "$DATA/.pom_secret"
ok "Settings saved in backend/.env (readable only by you)"

# ------------------------------------------------------------ keep the backend running
say "Starting the backend (and at every login)"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$BACKEND/.venv/bin/uvicorn</string><string>app.main:app</string>
    <string>--host</string><string>127.0.0.1</string><string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$BACKEND</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOGS/backend.log</string>
  <key>StandardErrorPath</key><string>$LOGS/backend.log</string>
</dict></plist>
PLIST
launchctl bootstrap "gui/$UID" "$PLIST"
for i in {1..30}; do curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 || die "The backend didn't start. See $LOGS/backend.log"
READY=$(curl -fsS "http://127.0.0.1:$PORT/readyz")
ok "Backend running on http://127.0.0.1:$PORT  ($READY)"

# ------------------------------------------------------------ desktop app
say "Building Bea.app"
"$ROOT/mac/build-mac.sh"
APP_PATH="$(cat "$ROOT/desktop/dist/.installed-path")"

# Hand the app its server address and token once; the app encrypts the token in your keychain and deletes this file.
NAME=$(ask "What should Bea call you? " "")
WAKE=$(ask "Listen for the wake word \"Hey Bea\"? [Y/n] " "y")
"$PY" - "$APPDATA/provision.json" "http://127.0.0.1:$PORT" "$TOKEN" "$NAME" "$WAKE" <<'PY'
import json, os, sys
path, url, token, name, wake = sys.argv[1:]
with open(path, "w") as f:
    json.dump({"backendUrl": url, "token": token, "userName": name, "launchAtLogin": True,
               "agentMode": True, "useBuiltInServer": False,  # this script runs the backend itself
               "wakeWord": not wake.lower().startswith("n")}, f)
os.chmod(path, 0o600)
PY
ok "App connected to the backend"

say "Launching Bea"
open "$APP_PATH"
cat <<DONE

  Bea is on your desktop.
  • Double-click her or press ⌘⇧Space to chat. Say "Hey Bea" to talk. Right-click for moves.
  • Give her tasks: she plans, researches, uses her browser and your apps, and asks before acting.
  • Bea Hub (menu bar icon > Bea Hub) shows her activity, approvals, goals, schedules and connections.
  • She lives in the menu bar too (the little blue silhouette).
  • The first time she listens, macOS asks for microphone permission. Allow it.

  Backend logs:   $LOGS/backend.log
  API keys:       $BACKEND/.env   (then: launchctl kickstart -k gui/$UID/$LABEL)
  Uninstall:      ./mac/uninstall.sh
DONE
