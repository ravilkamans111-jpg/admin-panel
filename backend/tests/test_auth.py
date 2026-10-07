"""Login mirrors the monolith's Django admin: credentials are a brand's own
`auth_user` row (PBKDF2), `is_active and is_staff` gate, role from permissions."""

from __future__ import annotations

import base64
import hashlib

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.django_password import verify_django_password
from app.models.tenant import TenantBase
from app.services import audit_service, auth_service, login_throttle

pytestmark = pytest.mark.asyncio


def django_hash(password: str, *, iterations: int = 600_000, salt: str = "salty") -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${base64.b64encode(digest).decode()}"


PASSWORD = "correct-horse-battery-staple"

BRAND_SCHEMA = [
    "ALTER TABLE auth_user ADD COLUMN password VARCHAR(128) NOT NULL DEFAULT ''",
    "CREATE TABLE auth_permission (id INTEGER PRIMARY KEY, codename VARCHAR(100))",
    "CREATE TABLE auth_user_user_permissions (user_id INTEGER, permission_id INTEGER)",
    "CREATE TABLE auth_group_permissions (group_id INTEGER, permission_id INTEGER)",
    "CREATE TABLE auth_user_groups (user_id INTEGER, group_id INTEGER)",
    "CREATE TABLE django_content_type (id INTEGER PRIMARY KEY, app_label VARCHAR, model VARCHAR)",
    (
        "CREATE TABLE django_admin_log (id INTEGER PRIMARY KEY AUTOINCREMENT, action_time TIMESTAMP, object_id TEXT,"
        " object_repr VARCHAR(200), action_flag SMALLINT, change_message TEXT, content_type_id INTEGER, user_id INTEGER)"
    ),
    "INSERT INTO auth_permission (id, codename) VALUES (1, 'view_transaction'), (2, 'change_transaction')",
]


async def add_user(session, username, *, password=PASSWORD, active=True, staff=True, superuser=False, perms=()):
    await session.execute(
        text(
            "INSERT INTO auth_user (username, first_name, last_name, email, is_active, is_staff, is_superuser,"
            " date_joined, password) VALUES (:u, '', '', '', :a, :s, :su, '2026-01-01', :p)"
        ),
        {"u": username, "a": active, "s": staff, "su": superuser, "p": django_hash(password)},
    )
    user_id = (await session.execute(text("SELECT id FROM auth_user WHERE username = :u"), {"u": username})).scalar_one()
    for perm_id in perms:
        await session.execute(
            text("INSERT INTO auth_user_user_permissions VALUES (:u, :p)"), {"u": user_id, "p": perm_id}
        )
    await session.commit()
    return user_id


@pytest_asyncio.fixture
async def brand_db(monkeypatch):
    """One in-memory DB standing in for every brand's Django database."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)
        for statement in BRAND_SCHEMA:
            await conn.execute(text(statement))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    for module in (auth_service, audit_service):
        monkeypatch.setattr(module, "get_tenant_sessionmaker", lambda brand_id: factory)

    async def no_redis():
        return None

    monkeypatch.setattr(login_throttle, "_redis", no_redis)
    login_throttle._memory.clear()
    async with factory() as session:
        yield session
    await engine.dispose()


async def login(client: AsyncClient, username: str, password: str = PASSWORD):
    return await client.post("/auth/login", json={"username": username, "password": password})


async def tokens(client: AsyncClient, username: str, brand: str = "ampay", password: str = PASSWORD) -> dict:
    resp = await login(client, username, password)
    assert resp.status_code == 200, resp.text
    selected = await client.post(
        "/auth/select-brand", json={"brand_id": brand},
        headers={"Authorization": f"Bearer {resp.json()['pre_auth_token']}"},
    )
    assert selected.status_code == 200, selected.text
    return selected.json()


# --- password verification -------------------------------------------------


def test_django_pbkdf2_hash_roundtrip_and_failures():
    encoded = django_hash("s3cret", iterations=1000)
    assert verify_django_password("s3cret", encoded)
    assert not verify_django_password("nope", encoded)
    assert not verify_django_password("s3cret", "!unusable")
    assert not verify_django_password("s3cret", "")
    assert not verify_django_password("s3cret", "md5$salt$abc")  # unknown algorithm fails closed
    assert not verify_django_password("s3cret", "pbkdf2_sha256$notanumber$salt$abc")


# --- login / roles -----------------------------------------------------------


async def test_superuser_gets_every_brand_as_superadmin(raw_client, brand_db):
    await add_user(brand_db, "root", staff=True, superuser=True)
    body = (await login(raw_client, "root")).json()
    assert {b["brand_id"] for b in body["available_brands"]} == {"ampay", "rajapay", "quiet-forest"}
    assert {b["role"] for b in body["available_brands"]} == {"superadmin"}


async def test_role_follows_django_permissions(raw_client, brand_db):
    await add_user(brand_db, "editor", perms=[2])
    await add_user(brand_db, "reader", perms=[1])
    await add_user(brand_db, "nobody")
    assert (await login(raw_client, "editor")).json()["available_brands"][0]["role"] == "operator"
    assert (await login(raw_client, "reader")).json()["available_brands"][0]["role"] == "viewer"
    assert (await login(raw_client, "nobody")).json()["available_brands"][0]["role"] == "viewer"


async def test_group_permissions_count_too(raw_client, brand_db):
    user_id = await add_user(brand_db, "grouped")
    await brand_db.execute(text("INSERT INTO auth_user_groups VALUES (:u, 7)"), {"u": user_id})
    await brand_db.execute(text("INSERT INTO auth_group_permissions VALUES (7, 2)"))
    await brand_db.commit()
    assert (await login(raw_client, "grouped")).json()["available_brands"][0]["role"] == "operator"


async def test_wrong_password_unknown_user_inactive_and_non_staff_are_all_401(raw_client, brand_db):
    await add_user(brand_db, "alice")
    await add_user(brand_db, "gone", active=False)
    await add_user(brand_db, "customer", staff=False)
    for username, password in (("alice", "wrong"), ("nobody", PASSWORD), ("gone", PASSWORD), ("customer", PASSWORD)):
        assert (await login(raw_client, username, password)).status_code == 401
    assert (await login(raw_client, "alice", "wrong")).json() == (await login(raw_client, "nobody", PASSWORD)).json()


async def test_username_is_exact_like_django(raw_client, brand_db):
    await add_user(brand_db, "Alice")
    assert (await login(raw_client, "alice")).status_code == 401
    assert (await login(raw_client, "Alice")).status_code == 200


async def test_old_builtin_credentials_do_not_exist(raw_client, brand_db):
    assert (await login(raw_client, "admin@example.com", "SuperSecret123!")).status_code == 401


# --- lockout ------------------------------------------------------------------


async def test_repeated_failures_lock_even_the_right_password(raw_client, brand_db):
    from app.core.settings_env import env_settings

    await add_user(brand_db, "alice")
    for _ in range(env_settings.login_max_failed_attempts):
        assert (await login(raw_client, "alice", "wrong")).status_code == 401
    assert (await login(raw_client, "alice")).status_code == 429


async def test_success_resets_the_counter(raw_client, brand_db):
    from app.core.settings_env import env_settings

    await add_user(brand_db, "alice")
    for _ in range(env_settings.login_max_failed_attempts - 1):
        await login(raw_client, "alice", "wrong")
    assert (await login(raw_client, "alice")).status_code == 200
    for _ in range(env_settings.login_max_failed_attempts - 1):
        assert (await login(raw_client, "alice", "wrong")).status_code == 401
    assert (await login(raw_client, "alice")).status_code == 200


# --- select-brand / refresh -----------------------------------------------------


async def test_select_brand_issues_scoped_tokens_with_the_brands_own_user_id(raw_client, brand_db):
    user_id = await add_user(brand_db, "alice", perms=[2])
    pair = await tokens(raw_client, "alice", "rajapay")
    assert (pair["brand_id"], pair["role"]) == ("rajapay", "operator")
    from app.core.security import TokenScope, decode_token

    assert int(decode_token(pair["access_token"], TokenScope.ACCESS).sub) == user_id


async def test_select_brand_rejects_a_brand_the_credentials_were_not_valid_in(raw_client, brand_db, monkeypatch):
    await add_user(brand_db, "alice")
    login_resp = (await login(raw_client, "alice")).json()
    # Pretend rajapay's DB has no such staff user: forge-proof because the token's brand map decides.
    from app.core.security import create_pre_auth_token

    narrowed = create_pre_auth_token("alice", {"ampay": 1})
    resp = await raw_client.post(
        "/auth/select-brand", json={"brand_id": "rajapay"}, headers={"Authorization": f"Bearer {narrowed}"}
    )
    assert resp.status_code == 403
    assert login_resp["pre_auth_token"]


async def test_select_brand_unknown_brand_404(raw_client, brand_db):
    await add_user(brand_db, "alice")
    pre = (await login(raw_client, "alice")).json()["pre_auth_token"]
    resp = await raw_client.post("/auth/select-brand", json={"brand_id": "nope"}, headers={"Authorization": f"Bearer {pre}"})
    assert resp.status_code == 404


async def test_refresh_works_then_dies_on_deactivation(raw_client, brand_db):
    await add_user(brand_db, "alice")
    pair = await tokens(raw_client, "alice")
    assert (await raw_client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})).status_code == 200
    await brand_db.execute(text("UPDATE auth_user SET is_active = 0 WHERE username = 'alice'"))
    await brand_db.commit()
    assert (await raw_client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})).status_code == 401


async def test_refresh_dies_when_the_password_changes_in_the_monolith(raw_client, brand_db):
    await add_user(brand_db, "alice")
    pair = await tokens(raw_client, "alice")
    await brand_db.execute(
        text("UPDATE auth_user SET password = :p WHERE username = 'alice'"), {"p": django_hash("new-password-1", salt="other")}
    )
    await brand_db.commit()
    assert (await raw_client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})).status_code == 401


async def test_refresh_picks_up_a_role_change(raw_client, brand_db):
    user_id = await add_user(brand_db, "alice", perms=[1])
    pair = await tokens(raw_client, "alice")
    assert pair["role"] == "viewer"
    await brand_db.execute(text("UPDATE auth_user SET is_superuser = 1 WHERE id = :i"), {"i": user_id})
    await brand_db.commit()
    resp = await raw_client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert resp.json()["role"] == "superadmin"


async def test_one_unreachable_brand_does_not_block_login(raw_client, brand_db, monkeypatch):
    await add_user(brand_db, "alice")
    real = auth_service.get_tenant_sessionmaker

    def flaky(brand_id):
        if brand_id == "rajapay":
            raise ConnectionError("db down")
        return real(brand_id)

    monkeypatch.setattr(auth_service, "get_tenant_sessionmaker", flaky)
    body = (await login(raw_client, "alice")).json()
    assert {b["brand_id"] for b in body["available_brands"]} == {"ampay", "quiet-forest"}


async def test_admin_endpoint_rejects_missing_token(raw_client):
    assert (await raw_client.get("/admin/schema")).status_code in (401, 403)


async def test_pre_auth_token_cannot_be_used_as_access_token(raw_client, brand_db):
    await add_user(brand_db, "alice")
    pre = (await login(raw_client, "alice")).json()["pre_auth_token"]
    assert (await raw_client.get("/admin/schema", headers={"Authorization": f"Bearer {pre}"})).status_code == 401


# --- audit into django_admin_log ------------------------------------------------


async def test_audit_writes_django_admin_log_in_django_format(brand_db):
    user_id = await add_user(brand_db, "alice")
    await brand_db.execute(text("INSERT INTO django_content_type VALUES (29, 'personal_account_transaction', 'transaction')"))
    await brand_db.commit()
    await audit_service.write_record_change_audit(
        admin_user_id=user_id, brand_id="ampay", action="update_transaction", model_key="transactions",
        record_id=505, before={"status": "ACCEPTED", "amount": "1"}, after={"status": "SUCCESS", "amount": "1"},
    )
    row = (await brand_db.execute(text("SELECT object_id, object_repr, action_flag, change_message, content_type_id, user_id FROM django_admin_log"))).one()
    assert row.object_id == "505" and row.action_flag == 2 and row.user_id == user_id and row.content_type_id == 29
    assert row.change_message == '[{"changed": {"fields": ["status"]}}]'


async def test_audit_failure_never_raises(monkeypatch):
    def broken(brand_id):
        raise ConnectionError("db down")

    monkeypatch.setattr(audit_service, "get_tenant_sessionmaker", broken)
    await audit_service.write_record_change_audit(
        admin_user_id=1, brand_id="ampay", action="create_record", model_key="banks", record_id=1, before={}, after={},
    )


async def test_audit_masks_secrets(brand_db):
    user_id = await add_user(brand_db, "alice")
    await audit_service.write_record_change_audit(
        admin_user_id=user_id, brand_id="ampay", action="update_record", model_key="merchants", record_id=2,
        before={"private_key": "old"}, after={"private_key": "new"},
    )
    message = (await brand_db.execute(text("SELECT change_message FROM django_admin_log"))).scalar_one()
    assert "old" not in message and "new" not in message
