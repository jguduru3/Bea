#!/bin/bash
# Build Bea.app for this Mac (Apple silicon or Intel): an all-in-one app with Bea's own server and
# browser inside. Signs it ad hoc so macOS runs it, installs it to Applications and makes a .dmg and
# a zipped .app you can share.
#   ./mac/build-mac.sh            build + install
#   ./mac/build-mac.sh --no-install
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP="$ROOT/desktop"
INSTALL=1; [[ "${1:-}" == "--no-install" ]] && INSTALL=0

say() { printf "\033[1;34m▸\033[0m %s\n" "$*"; }
die() { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

[[ "$(uname)" == "Darwin" ]] || die "Run this on a Mac."
command -v node >/dev/null || die "Node.js 20+ is needed. Install it from https://nodejs.org or with: brew install node"
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
(( NODE_MAJOR >= 20 )) || die "Node.js 20 or newer is needed (found $(node -v))."

case "$(uname -m)" in arm64) ARCH=arm64; OUT="mac-arm64" ;; *) ARCH=x64; OUT="mac" ;; esac

# Bea's built-in server (Python, frozen with PyInstaller) and her browser
if [[ ! -x "$ROOT/backend/dist/bea-server/bea-server" ]]; then
  say "Building Bea's built-in server (a few minutes)…"
  PY="$ROOT/backend/.venv/bin/python"
  if [[ ! -x "$PY" ]]; then
    for c in python3.13 python3.12 python3.11 python3.10 python3; do command -v "$c" >/dev/null && { "$c" -m venv "$ROOT/backend/.venv"; break; }; done
  fi
  [[ -x "$PY" ]] || die "Python 3.10+ is needed to build Bea's server (brew install python@3.12)."
  "$PY" -m pip install --quiet --upgrade pip
  # ready-made cryptography wheels bundle their own OpenSSL; a source build links one PyInstaller can clash with
  "$PY" -m pip install --quiet --only-binary=cryptography -e "$ROOT/backend" "pyinstaller>=6.16"
  (cd "$ROOT/backend" && "$PY" build_server.py)
fi

cd "$DESKTOP"
say "Installing app dependencies…"
if [[ -f package-lock.json ]]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
[[ -x node_modules/electron/dist/Electron.app/Contents/MacOS/Electron ]] || node node_modules/electron/install.js
say "Getting the wake word model…"
npm run --silent fetch-wake-model

say "Building Bea.app for ${ARCH}…"
CSC_IDENTITY_AUTO_DISCOVERY=false npx electron-builder --mac dir --"$ARCH" --publish never

APP="$DESKTOP/dist/$OUT/Bea.app"
[[ -d "$APP" ]] || die "Build finished but $APP was not found."

say "Signing ad hoc…"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
codesign --verify --deep "$APP" && say "Signature OK"

DMG="$DESKTOP/dist/Bea-$ARCH.dmg"
say "Making ${DMG}…"
STAGE="$(mktemp -d)"; cp -R "$APP" "$STAGE/"; ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Bea" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
ditto -c -k --keepParent "$APP" "$DESKTOP/dist/Bea-$ARCH.app.zip"
say "Made $DMG and $DESKTOP/dist/Bea-$ARCH.app.zip"

if (( INSTALL )); then
  DEST="/Applications"
  [[ -w "$DEST" ]] || DEST="$HOME/Applications"
  mkdir -p "$DEST"
  pkill -x Bea 2>/dev/null || true; pkill -x Pom 2>/dev/null || true
  say "Installing to $DEST/Bea.app…"
  ditto "$APP" "$DEST/Bea.app"
  echo "$DEST/Bea.app" > "$DESKTOP/dist/.installed-path"
fi
say "Done."
