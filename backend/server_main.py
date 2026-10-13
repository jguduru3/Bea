"""Entry point for the standalone Bea server that ships inside the desktop installers.

The desktop app starts this program (built with PyInstaller, see build_server.py) and passes all
settings as POM_* environment variables: where to keep the database and secrets, the device token,
and so on. It can also be run by hand:

    bea-server --port 47821              serve on 127.0.0.1:47821
    bea-server --self-test               check that everything bundled works, then exit
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path


def _here() -> Path:
    # PyInstaller unpacks next to the executable (onedir); in development this is backend/.
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _use_bundled_browser() -> None:
    """Point Playwright at the Chromium shipped with the installer, if there is one."""
    bundled = Path(sys.executable).resolve().parent / "browsers"
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and bundled.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)


async def _self_test() -> int:
    """Exercise every piece that packaging can break. Prints one line per check."""
    ok = True

    def report(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {name}{'  ' + detail if detail else ''}", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        os.environ.update({
            "POM_DATABASE_URL": f"sqlite+aiosqlite:///{Path(tmp, 'selftest.db').as_posix()}",
            "POM_SECRET_FILE": str(Path(tmp, "secret")), "POM_DATA_DIR": str(Path(tmp, "data")),
            "POM_BOOTSTRAP_DEVICE_TOKEN": "pom_selftest_token_1234567890", "POM_ALLOW_DEVICE_KEY_MANAGEMENT": "true",
            "POM_LOG_LEVEL": "WARNING",
        })
        try:
            import httpx
            from app.config import Settings
            from app.main import create_app

            app = create_app(Settings(_env_file=None))
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                    report("server starts, database ready", (await c.get("/readyz")).json().get("database") is True)
                    auth = {"Authorization": "Bearer pom_selftest_token_1234567890"}
                    st = (await c.get("/v1/agent/status", headers=auth)).json()
                    report("agent tools registered", len(st.get("tools", [])) >= 26, f"{len(st.get('tools', []))} tools")
                    report("key management", (await c.get("/v1/keys", headers=auth)).status_code == 200)
        except Exception as exc:  # noqa: BLE001
            report("server starts", False, repr(exc))

        try:
            from app.keys import encrypt, _fernet
            from app.config import Settings
            s = Settings(_env_file=None)
            report("encryption", _fernet(s).decrypt(encrypt(s, "hello").encode()) == b"hello")
        except Exception as exc:  # noqa: BLE001
            report("encryption", False, repr(exc))

        try:
            from app.agent.schedule import next_run
            from datetime import datetime, timezone
            nxt = next_run("daily 09:00", "Asia/Kolkata", datetime(2026, 1, 1, tzinfo=timezone.utc))
            report("time zones", nxt.hour == 3 and nxt.minute == 30)
        except Exception as exc:  # noqa: BLE001
            report("time zones", False, repr(exc))

        try:
            import anthropic
            import jsonschema
            jsonschema.validate({"a": 1}, {"type": "object"})
            report("Claude SDK and schemas", True, f"anthropic {anthropic.__version__}")
        except Exception as exc:  # noqa: BLE001
            report("Claude SDK and schemas", False, repr(exc))

        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.set_content("<h1>Bea</h1>")
                report("Bea's browser (Chromium)", await page.inner_text("h1") == "Bea", os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "default location"))
                await browser.close()
        except Exception as exc:  # noqa: BLE001
            report("Bea's browser (Chromium)", False, repr(exc)[:300])

    print("SELF-TEST " + ("PASSED" if ok else "FAILED"), flush=True)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="bea-server", description="Bea's backend server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BEA_PORT", "47821")))
    parser.add_argument("--self-test", action="store_true", help="check the bundled parts and exit")
    args = parser.parse_args()

    _use_bundled_browser()
    if args.self_test:
        return asyncio.run(_self_test())

    import uvicorn
    from app.main import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level=os.environ.get("POM_LOG_LEVEL", "info").lower(),
                access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
