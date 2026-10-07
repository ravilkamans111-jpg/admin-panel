"""Adds a test staff superuser to each brand's LOCAL Django database so the
login flow can be tried by hand (`auth_user`, PBKDF2 — what Django itself writes).

    cd backend && python scripts/seed_test_staff.py            # create / reset
    cd backend && python scripts/seed_test_staff.py --remove   # delete it again

Refuses to touch any host that isn't local. Connection settings come from
backend/.env (`BRAND_<BRAND>_DB_*`). Login: username `test_admin`, password
TEST_PASSWORD below (or $TEST_ADMIN_PASSWORD) — a throwaway for local test data.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

USERNAME = "test_admin"
TEST_PASSWORD = "Local-Test-Admin-2026"
BRANDS = ("AMPAY", "RAJAPAY", "QUIET_FOREST")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "host.docker.internal"}


def django_pbkdf2(password: str, iterations: int = 870_000) -> str:
    salt = secrets.token_urlsafe(12)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${base64.b64encode(digest).decode()}"


async def run(remove: bool) -> None:
    env = {**dotenv_values(Path(__file__).resolve().parent.parent / ".env"), **os.environ}
    password = env.get("TEST_ADMIN_PASSWORD") or TEST_PASSWORD
    for brand in BRANDS:
        host = env[f"BRAND_{brand}_DB_HOST"]
        if host not in LOCAL_HOSTS:
            print(f"{brand}: refusing non-local host {host!r}")
            continue
        dsn = (
            f"postgresql+asyncpg://{env[f'BRAND_{brand}_DB_USER']}:{env[f'BRAND_{brand}_DB_PASSWORD']}"
            f"@{host}:{env.get(f'BRAND_{brand}_DB_PORT', '5432')}/{env[f'BRAND_{brand}_DB_NAME']}"
        )
        engine = create_async_engine(dsn)
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM django_admin_log WHERE user_id IN (SELECT id FROM auth_user WHERE username = :u)"),
                {"u": USERNAME},
            )
            if remove:
                await conn.execute(text("DELETE FROM auth_user WHERE username = :u"), {"u": USERNAME})
                print(f"{brand}: removed")
            else:
                exists = (await conn.execute(text("SELECT 1 FROM auth_user WHERE username = :u"), {"u": USERNAME})).first()
                params = {"u": USERNAME, "p": django_pbkdf2(password)}
                if exists:
                    await conn.execute(
                        text("UPDATE auth_user SET password=:p, is_active=true, is_staff=true, is_superuser=true WHERE username=:u"),
                        params,
                    )
                else:
                    await conn.execute(
                        text(
                            "INSERT INTO auth_user (password, is_superuser, username, first_name, last_name, email,"
                            " is_staff, is_active, date_joined) VALUES (:p, true, :u, '', '', '', true, true, now())"
                        ),
                        params,
                    )
                print(f"{brand}: {'updated' if exists else 'created'} {USERNAME}")
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run("--remove" in sys.argv[1:]))
