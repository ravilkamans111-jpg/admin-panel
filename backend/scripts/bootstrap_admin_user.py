"""Creates (or resets) a superuser account in the control-plane DB.

The only way to get the first account into a fresh deployment — there are no
built-in credentials. Run once after `alembic upgrade head`:

    cd backend
    python scripts/bootstrap_admin_user.py you@company.com
    # password is prompted (hidden); or set BOOTSTRAP_ADMIN_PASSWORD for CI

Inside the container (scripts are baked into the image at /app/scripts):

    docker compose exec backend python scripts/bootstrap_admin_user.py you@company.com

Re-running for an existing email resets its password, re-activates it, clears
any lockout and revokes its existing sessions. Everyone else should be created
through the staff API / admin UI by a superuser.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here.parent))

from pydantic import EmailStr, TypeAdapter, ValidationError

from app.core.security import WeakPasswordError, hash_password, validate_password_strength
from app.core.settings_env import env_settings
from app.db.control_plane import ControlPlaneSessionLocal
from app.models.control_plane import AdminUser
from app.repositories import control_plane_repository as repo


async def bootstrap(email: str, password: str) -> None:
    email = email.strip().lower()
    # Same validation the login endpoint applies, so we never create an account that can't log in.
    email = TypeAdapter(EmailStr).validate_python(email)
    validate_password_strength(password, min_length=env_settings.min_password_length, email=email)
    async with ControlPlaneSessionLocal() as session:
        user = await repo.get_admin_user_by_email(session, email)
        if user is None:
            user = AdminUser(email=email, full_name="Superadmin", password_hash="")
            session.add(user)
        else:
            user.token_version += 1
        user.password_hash = hash_password(password)
        user.password_changed_at = datetime.now(UTC)
        user.is_superuser = True
        user.is_active = True
        user.failed_login_attempts = 0
        user.locked_until = None
        await session.flush()
        await repo.write_audit_log_no_commit(
            session, admin_user_id=user.id, brand_id=None, action="bootstrap_superuser", detail={"email": email}
        )
        await session.commit()
    print(f"Superuser ready: {email}")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: bootstrap_admin_user.py <email>")
        sys.exit(1)
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match")
            sys.exit(1)
    try:
        asyncio.run(bootstrap(sys.argv[1], password))
    except (WeakPasswordError, ValidationError) as exc:
        print(f"Rejected: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
