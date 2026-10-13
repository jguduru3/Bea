"""Command line helpers.

    python -m app.cli create-token "Rahul's laptop"
    python -m app.cli list-tokens
    python -m app.cli revoke-token <id>
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from .config import get_settings
from .db import ApiToken, Database
from .security import create_token


async def _run(args: argparse.Namespace) -> None:
    db = Database(get_settings().database_url)
    await db.create_all()
    async with db.sessionmaker() as s:
        if args.cmd == "create-token":
            row, raw = await create_token(s, args.name)
            print(f"Created token for {row.name!r} (id {row.id}).")
            print("Paste this into Pom's settings. It will not be shown again:\n")
            print(raw)
        elif args.cmd == "list-tokens":
            for t in (await s.execute(select(ApiToken).order_by(ApiToken.created_at))).scalars():
                state = "revoked" if t.revoked else "active"
                print(f"{t.id}  {t.prefix}…  {state:8}  {t.name}  last used: {t.last_used_at or 'never'}")
        elif args.cmd == "revoke-token":
            row = await s.get(ApiToken, args.id)
            if not row:
                raise SystemExit("No token with that id")
            row.revoked = True
            await s.commit()
            print(f"Revoked {row.name!r}")
    await db.dispose()


def main() -> None:
    p = argparse.ArgumentParser(prog="pom")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-token", help="Create a device token")
    c.add_argument("name")
    sub.add_parser("list-tokens", help="List device tokens")
    r = sub.add_parser("revoke-token", help="Revoke a device token")
    r.add_argument("id")
    asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    main()
