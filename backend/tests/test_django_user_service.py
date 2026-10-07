"""Django-admin-style "Add user": validation messages and the stored hash format."""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("USE_LOCAL_ENV_SECRETS", "true")

from app.core.django_password import verify_django_password
from app.models.tenant import TenantBase
from app.services import django_user_service
from app.services.django_user_service import UserValidationError


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)
        await conn.execute(text("ALTER TABLE auth_user ADD COLUMN password VARCHAR(128) NOT NULL DEFAULT ''"))
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def test_creates_active_non_staff_user_with_a_django_compatible_hash(session):
    created = await django_user_service.create_user(
        session, username="new.user", password1="Strong-Pass-9", password2="Strong-Pass-9"
    )
    row = (await session.execute(text("SELECT password, is_active, is_staff, is_superuser FROM auth_user"))).one()
    assert (row.is_active, row.is_staff, row.is_superuser) == (1, 0, 0)
    assert row.password.startswith("pbkdf2_sha256$")
    assert verify_django_password("Strong-Pass-9", row.password)
    assert created["username"] == "new.user"


@pytest.mark.parametrize(
    ("username", "p1", "p2", "field"),
    [
        ("", "Strong-Pass-9", "Strong-Pass-9", "username"),
        ("bad name!", "Strong-Pass-9", "Strong-Pass-9", "username"),
        ("ok", "short", "short", "password1"),
        ("ok", "12345678901", "12345678901", "password1"),
        ("ok", "password123", "password123", "password1"),
        ("johnsmith", "johnsmith1", "johnsmith1", "password1"),
        ("ok", "Strong-Pass-9", "Different-Pass-9", "password2"),
    ],
)
async def test_validation_matches_django_rules(session, username, p1, p2, field):
    with pytest.raises(UserValidationError) as exc:
        await django_user_service.create_user(session, username=username, password1=p1, password2=p2)
    assert field in exc.value.errors


async def test_duplicate_username_is_rejected(session):
    await django_user_service.create_user(session, username="dup", password1="Strong-Pass-9", password2="Strong-Pass-9")
    with pytest.raises(UserValidationError) as exc:
        await django_user_service.create_user(session, username="dup", password1="Strong-Pass-9", password2="Strong-Pass-9")
    assert "username" in exc.value.errors
