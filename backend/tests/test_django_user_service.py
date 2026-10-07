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


# --- Change user form -------------------------------------------------------------------------

EXTRA_SCHEMA = [
    "CREATE TABLE auth_group (id INTEGER PRIMARY KEY, name VARCHAR(150))",
    "CREATE TABLE auth_permission (id INTEGER PRIMARY KEY, name VARCHAR(255), content_type_id INTEGER, codename VARCHAR(100))",
    "CREATE TABLE django_content_type (id INTEGER PRIMARY KEY, app_label VARCHAR, model VARCHAR)",
    "CREATE TABLE auth_user_groups (user_id INTEGER, group_id INTEGER)",
    "CREATE TABLE auth_user_user_permissions (user_id INTEGER, permission_id INTEGER)",
    "INSERT INTO auth_group VALUES (1, 'Support'), (2, 'Dev')",
    "INSERT INTO django_content_type VALUES (1, 'api_client', 'bank')",
    "INSERT INTO auth_permission VALUES (10, 'Can add Банк', 1, 'add_bank'), (11, 'Can change Банк', 1, 'change_bank')",
]


@pytest_asyncio.fixture
async def form_session(session):
    for statement in EXTRA_SCHEMA:
        await session.execute(text(statement))
    await session.commit()
    return session


async def _new_user(session, username="alice") -> int:
    created = await django_user_service.create_user(
        session, username=username, password1="Strong-Pass-9", password2="Strong-Pass-9"
    )
    return created["id"]


def _form(**overrides):
    base = {
        "username": "alice", "first_name": "Alice", "last_name": "A", "email": "a@x.io", "is_active": True,
        "is_staff": True, "is_superuser": False, "groups": [1], "permissions": [10, 11],
        "last_login": None, "date_joined": "2026-05-20T14:56:38",
    }
    return {**base, **overrides}


async def test_edit_data_has_masked_password_and_choices(form_session):
    user_id = await _new_user(form_session)
    data = await django_user_service.get_edit_data(form_session, user_id)
    password = data["user"]["password"]
    assert password["algorithm"] == "pbkdf2_sha256" and password["iterations"] == 870000
    assert set(password["salt"][6:]) == {"*"} and set(password["hash"][6:]) == {"*"}
    assert [g["label"] for g in data["groups"]["available"]] == ["Dev", "Support"]
    assert data["permissions"]["available"][0]["label"] == "api_client | Банк | Can add Банк"


async def test_update_saves_flags_groups_and_permissions(form_session):
    user_id = await _new_user(form_session)
    await django_user_service.update_user(form_session, actor_id=999, user_id=user_id, data=_form(username="alice2"))
    data = await django_user_service.get_edit_data(form_session, user_id)
    assert data["user"]["username"] == "alice2" and data["user"]["is_staff"] is True
    assert data["groups"]["chosen"] == [1] and sorted(data["permissions"]["chosen"]) == [10, 11]
    await django_user_service.update_user(
        form_session, actor_id=999, user_id=user_id, data=_form(username="alice2", groups=[], permissions=[11])
    )
    data = await django_user_service.get_edit_data(form_session, user_id)
    assert data["groups"]["chosen"] == [] and data["permissions"]["chosen"] == [11]


async def test_update_validation_and_self_lockout_guard(form_session):
    user_id = await _new_user(form_session)
    other = await _new_user(form_session, "bob")
    with pytest.raises(UserValidationError) as exc:
        await django_user_service.update_user(form_session, actor_id=999, user_id=user_id, data=_form(username="bob"))
    assert "username" in exc.value.errors
    with pytest.raises(UserValidationError) as exc:
        await django_user_service.update_user(form_session, actor_id=999, user_id=user_id, data=_form(date_joined="nope"))
    assert "date_joined" in exc.value.errors
    with pytest.raises(UserValidationError) as exc:  # a superuser can't switch off their own access
        await django_user_service.update_user(form_session, actor_id=other, user_id=other, data=_form(username="bob", is_staff=False))
    assert "is_active" in exc.value.errors


async def test_set_password_validates_and_changes_the_hash(form_session):
    user_id = await _new_user(form_session)
    old = (await form_session.execute(text("SELECT password FROM auth_user WHERE id = :i"), {"i": user_id})).scalar_one()
    with pytest.raises(UserValidationError):
        await django_user_service.set_password(form_session, user_id=user_id, password1="short", password2="short")
    await django_user_service.set_password(form_session, user_id=user_id, password1="Brand-New-Pass-5", password2="Brand-New-Pass-5")
    new = (await form_session.execute(text("SELECT password FROM auth_user WHERE id = :i"), {"i": user_id})).scalar_one()
    assert new != old and verify_django_password("Brand-New-Pass-5", new)
