"""Build the standalone Bea server for the desktop installers (Windows, macOS).

    python build_server.py            -> backend/dist/bea-server/ (+ browsers/), self-tested

Steps: freeze the server with PyInstaller, add Playwright's headless Chromium into
dist/bea-server/browsers (reusing a local copy when one matches), then run the frozen program's
--self-test so a packaging mistake fails the build instead of a user's install.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist" / "bea-server"
EXE = DIST / ("bea-server.exe" if os.name == "nt" else "bea-server")


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def default_browser_cache() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def main() -> None:
    shutil.rmtree(HERE / "build", ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "bea-server.spec"], cwd=HERE)

    browsers = DIST / "browsers"
    browsers.mkdir(exist_ok=True)
    # Reuse a matching local download (saves ~110 MB); Playwright skips what's already there.
    cache = default_browser_cache()
    for d in cache.glob("chromium_headless_shell-*") if cache.is_dir() else []:
        shutil.copytree(d, browsers / d.name, dirs_exist_ok=True)
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers)}
    run([sys.executable, "-m", "playwright", "install", "--only-shell", "chromium"], env=env)
    # keep only the revision this Playwright version uses
    needed = subprocess.run([sys.executable, "-m", "playwright", "install", "--dry-run", "--only-shell", "chromium"],
                            env=env, capture_output=True, text=True).stdout
    for d in browsers.iterdir():
        if d.is_dir() and d.name not in needed:
            shutil.rmtree(d)

    run([str(EXE), "--self-test"], cwd=DIST)
    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file()) / 1e6
    print(f"\nBea server ready: {DIST}  ({size:.0f} MB)")


if __name__ == "__main__":
    main()
