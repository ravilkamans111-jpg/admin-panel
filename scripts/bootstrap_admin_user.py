"""Creates (or updates) a superuser admin account in the control-plane DB.

Usage:
    cd backend
    uv run python ../scripts/bootstrap_admin_user.py you@example.com 'a-strong-password'

A superuser sees every known brand (see app/core/brands.py) without needing
individual BrandAccess rows — use this only for the first account; grant
everyone else brand-scoped access explicitly (non-superuser AdminUser + one
BrandAccess row per brand they should see).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.control_plane import ControlPlaneSessionLocal  # noqa: E402
from app.models.control_plane import AdminUser  # noqa: E402


async def bootstrap(email: str, password: str) -> None:
    async with ControlPlaneSessionLocal() as session:
        result = await session.execute(select(AdminUser).where(AdminUser.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            user = AdminUser(email=email, full_name="Superadmin", is_superuser=True, is_active=True)
            session.add(user)
        user.password_hash = hash_password(password)
        user.is_superuser = True
        user.is_active = True
        await session.commit()
    print(f"Superuser ready: {email}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: bootstrap_admin_user.py <email> <password>")
        sys.exit(1)
    asyncio.run(bootstrap(sys.argv[1], sys.argv[2]))
