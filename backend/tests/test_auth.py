from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.security import hash_password
from app.models.control_plane import AdminUser, BrandAccess

pytestmark = pytest.mark.asyncio


async def test_login_wrong_password_rejected(raw_client: AsyncClient, seeded_admin_user):
    resp = await raw_client.post(
        "/auth/login", json={"email": "viewer@example.com", "password": "wrong-password"}
    )
    assert resp.status_code == 401


async def test_login_returns_available_brands(raw_client: AsyncClient, seeded_admin_user):
    resp = await raw_client.post(
        "/auth/login", json={"email": "viewer@example.com", "password": "correct-horse-battery-staple"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "pre_auth_token" in body
    assert [b["brand_id"] for b in body["available_brands"]] == ["ampay"]
    assert body["available_brands"][0]["role"] == "viewer"


async def test_select_brand_denied_for_unassigned_brand(raw_client: AsyncClient, seeded_admin_user):
    login_resp = await raw_client.post(
        "/auth/login", json={"email": "viewer@example.com", "password": "correct-horse-battery-staple"}
    )
    pre_auth_token = login_resp.json()["pre_auth_token"]

    resp = await raw_client.post(
        "/auth/select-brand",
        json={"brand_id": "rajapay"},
        headers={"Authorization": f"Bearer {pre_auth_token}"},
    )
    assert resp.status_code == 403


async def test_select_brand_success_issues_scoped_token(raw_client: AsyncClient, seeded_admin_user):
    login_resp = await raw_client.post(
        "/auth/login", json={"email": "viewer@example.com", "password": "correct-horse-battery-staple"}
    )
    pre_auth_token = login_resp.json()["pre_auth_token"]

    resp = await raw_client.post(
        "/auth/select-brand",
        json={"brand_id": "ampay"},
        headers={"Authorization": f"Bearer {pre_auth_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["brand_id"] == "ampay"
    assert body["role"] == "viewer"
    assert "access_token" in body
    assert "refresh_token" in body


async def test_admin_endpoint_rejects_missing_token(raw_client: AsyncClient):
    resp = await raw_client.get("/admin/schema")
    assert resp.status_code in (401, 403)


# --- DB-backed superuser, lockout, sessions, password change ---

PASSWORD = "correct-horse-battery-staple"


async def _login(client: AsyncClient, email: str, password: str = PASSWORD):
    return await client.post("/auth/login", json={"email": email, "password": password})


async def _tokens(client: AsyncClient, email: str, brand: str = "ampay", password: str = PASSWORD) -> dict:
    login_resp = await _login(client, email, password)
    assert login_resp.status_code == 200, login_resp.text
    resp = await client.post(
        "/auth/select-brand",
        json={"brand_id": brand},
        headers={"Authorization": f"Bearer {login_resp.json()['pre_auth_token']}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest_asyncio.fixture
async def seeded_superuser(control_plane_session):
    user = AdminUser(
        email="root@example.com", password_hash=hash_password(PASSWORD), full_name="Root",
        is_active=True, is_superuser=True,
    )
    control_plane_session.add(user)
    await control_plane_session.commit()
    await control_plane_session.refresh(user)
    return user


async def test_no_builtin_credentials_exist(raw_client: AsyncClient, control_plane_session):
    resp = await _login(raw_client, "admin@example.com", "SuperSecret123!")
    assert resp.status_code == 401


async def test_db_superuser_sees_all_brands_with_superadmin_role(raw_client, seeded_superuser):
    body = (await _login(raw_client, "root@example.com")).json()
    assert {b["brand_id"] for b in body["available_brands"]} == {"ampay", "rajapay", "quiet-forest"}
    assert all(b["role"] == "superadmin" for b in body["available_brands"])


async def test_email_login_is_case_insensitive(raw_client, seeded_superuser):
    assert (await _login(raw_client, "ROOT@Example.com")).status_code == 200


async def test_inactive_user_cannot_log_in(raw_client, seeded_admin_user, control_plane_session):
    seeded_admin_user.is_active = False
    await control_plane_session.commit()
    assert (await _login(raw_client, "viewer@example.com")).status_code == 401


async def test_account_locks_after_repeated_failures_even_with_correct_password(raw_client, seeded_admin_user):
    from app.core.settings_env import env_settings

    for _ in range(env_settings.login_max_failed_attempts):
        assert (await _login(raw_client, "viewer@example.com", "wrong")).status_code == 401
    locked = await _login(raw_client, "viewer@example.com")
    assert locked.status_code == 429


async def test_successful_login_resets_failed_counter(raw_client, seeded_admin_user, control_plane_session):
    for _ in range(2):
        await _login(raw_client, "viewer@example.com", "wrong")
    assert (await _login(raw_client, "viewer@example.com")).status_code == 200
    await control_plane_session.refresh(seeded_admin_user)
    assert seeded_admin_user.failed_login_attempts == 0
    assert seeded_admin_user.last_login_at is not None


async def test_unknown_email_is_indistinguishable_from_wrong_password(raw_client, seeded_admin_user):
    a = await _login(raw_client, "nobody@example.com", "whatever-password-1")
    b = await _login(raw_client, "viewer@example.com", "whatever-password-1")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


async def test_refresh_rejected_after_user_deactivated(raw_client, seeded_admin_user, control_plane_session):
    tokens = await _tokens(raw_client, "viewer@example.com")
    seeded_admin_user.is_active = False
    await control_plane_session.commit()
    resp = await raw_client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


async def test_refresh_rejected_after_brand_access_revoked(raw_client, seeded_admin_user, control_plane_session):
    from sqlalchemy import delete

    tokens = await _tokens(raw_client, "viewer@example.com")
    await control_plane_session.execute(delete(BrandAccess))
    await control_plane_session.commit()
    resp = await raw_client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 403


async def test_change_password_revokes_old_sessions_and_keeps_current(raw_client, seeded_admin_user):
    old = await _tokens(raw_client, "viewer@example.com")
    other_session = await _tokens(raw_client, "viewer@example.com")

    resp = await raw_client.post(
        "/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "a-much-better-passphrase-42"},
        headers=_bearer(old),
    )
    assert resp.status_code == 200
    fresh = resp.json()

    assert (await raw_client.post("/auth/refresh", json={"refresh_token": other_session["refresh_token"]})).status_code == 401
    assert (await raw_client.post("/auth/refresh", json={"refresh_token": fresh["refresh_token"]})).status_code == 200
    assert (await _login(raw_client, "viewer@example.com", PASSWORD)).status_code == 401
    assert (await _login(raw_client, "viewer@example.com", "a-much-better-passphrase-42")).status_code == 200


async def test_change_password_requires_correct_current_password(raw_client, seeded_admin_user):
    tokens = await _tokens(raw_client, "viewer@example.com")
    resp = await raw_client.post(
        "/auth/change-password",
        json={"current_password": "nope", "new_password": "a-much-better-passphrase-42"},
        headers=_bearer(tokens),
    )
    assert resp.status_code == 400


@pytest.mark.parametrize("weak", ["short1", "onlyletterslongenough", "123456789012345", "viewer-viewer-1"])
async def test_change_password_enforces_policy(raw_client, seeded_admin_user, weak):
    tokens = await _tokens(raw_client, "viewer@example.com")
    resp = await raw_client.post(
        "/auth/change-password", json={"current_password": PASSWORD, "new_password": weak}, headers=_bearer(tokens)
    )
    assert resp.status_code == 422


# --- Staff management ---


async def test_staff_endpoints_forbidden_for_non_superuser(raw_client, seeded_admin_user):
    tokens = await _tokens(raw_client, "viewer@example.com")
    assert (await raw_client.get("/staff/users", headers=_bearer(tokens))).status_code == 403


async def test_superuser_creates_staff_and_grants_brand_access(raw_client, seeded_superuser):
    tokens = await _tokens(raw_client, "root@example.com")
    resp = await raw_client.post(
        "/staff/users",
        json={
            "email": "Ops@Example.com", "full_name": "Ops", "password": "ops-passphrase-2026",
            "brand_access": {"rajapay": "operator"},
        },
        headers=_bearer(tokens),
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["email"] == "ops@example.com"
    assert created["brand_access"] == {"rajapay": "operator"}

    login = await _login(raw_client, "ops@example.com", "ops-passphrase-2026")
    assert [(b["brand_id"], b["role"]) for b in login.json()["available_brands"]] == [("rajapay", "operator")]

    resp = await raw_client.put(
        f"/staff/users/{created['id']}/brand-access/ampay", json={"role": "viewer"}, headers=_bearer(tokens)
    )
    assert resp.json()["brand_access"] == {"rajapay": "operator", "ampay": "viewer"}
    resp = await raw_client.put(
        f"/staff/users/{created['id']}/brand-access/rajapay", json={"role": None}, headers=_bearer(tokens)
    )
    assert resp.json()["brand_access"] == {"ampay": "viewer"}


async def test_staff_create_rejects_duplicate_weak_password_and_unknown_brand(raw_client, seeded_superuser):
    tokens = await _tokens(raw_client, "root@example.com")
    base = {"full_name": "X", "password": "ops-passphrase-2026"}
    ok = await raw_client.post("/staff/users", json={**base, "email": "x@example.com"}, headers=_bearer(tokens))
    assert ok.status_code == 201
    dup = await raw_client.post("/staff/users", json={**base, "email": "X@example.com"}, headers=_bearer(tokens))
    assert dup.status_code == 409
    weak = await raw_client.post(
        "/staff/users", json={"email": "y@example.com", "full_name": "Y", "password": "weak"}, headers=_bearer(tokens)
    )
    assert weak.status_code == 422
    bad_brand = await raw_client.post(
        "/staff/users", json={**base, "email": "z@example.com", "brand_access": {"nope": "viewer"}}, headers=_bearer(tokens)
    )
    assert bad_brand.status_code == 404


async def test_deactivating_user_kills_refresh_and_login(raw_client, seeded_superuser, seeded_admin_user):
    admin = await _tokens(raw_client, "root@example.com")
    victim = await _tokens(raw_client, "viewer@example.com")
    resp = await raw_client.patch(
        f"/staff/users/{seeded_admin_user.id}", json={"is_active": False}, headers=_bearer(admin)
    )
    assert resp.status_code == 200
    assert (await raw_client.post("/auth/refresh", json={"refresh_token": victim["refresh_token"]})).status_code == 401
    assert (await _login(raw_client, "viewer@example.com")).status_code == 401


async def test_superuser_cannot_deactivate_or_demote_self(raw_client, seeded_superuser):
    tokens = await _tokens(raw_client, "root@example.com")
    for body in ({"is_active": False}, {"is_superuser": False}):
        resp = await raw_client.patch(f"/staff/users/{seeded_superuser.id}", json=body, headers=_bearer(tokens))
        assert resp.status_code == 400


async def test_cannot_demote_last_active_superuser(raw_client, seeded_superuser, control_plane_session):
    tokens = await _tokens(raw_client, "root@example.com")
    other = AdminUser(
        email="second@example.com", password_hash=hash_password(PASSWORD), is_active=True, is_superuser=True
    )
    control_plane_session.add(other)
    await control_plane_session.commit()
    # demoting the other superuser is fine while root remains...
    assert (
        await raw_client.patch(f"/staff/users/{other.id}", json={"is_superuser": False}, headers=_bearer(tokens))
    ).status_code == 200
    # ...but deactivating the only remaining one (self) is blocked by the self-guard.
    assert (
        await raw_client.patch(f"/staff/users/{seeded_superuser.id}", json={"is_active": False}, headers=_bearer(tokens))
    ).status_code == 400


async def test_reset_password_and_unlock(raw_client, seeded_superuser, seeded_admin_user):
    from app.core.settings_env import env_settings

    admin = await _tokens(raw_client, "root@example.com")
    for _ in range(env_settings.login_max_failed_attempts):
        await _login(raw_client, "viewer@example.com", "wrong")
    assert (await _login(raw_client, "viewer@example.com")).status_code == 429

    resp = await raw_client.post(
        f"/staff/users/{seeded_admin_user.id}/reset-password",
        json={"new_password": "reset-passphrase-2026"}, headers=_bearer(admin),
    )
    assert resp.status_code == 204
    assert (await _login(raw_client, "viewer@example.com", "reset-passphrase-2026")).status_code == 200


async def test_staff_actions_are_audited(raw_client, seeded_superuser, control_plane_session):
    from sqlalchemy import select

    from app.models.control_plane import AuditLog

    tokens = await _tokens(raw_client, "root@example.com")
    await raw_client.post(
        "/staff/users",
        json={"email": "a@example.com", "full_name": "A", "password": "audited-passphrase-1"},
        headers=_bearer(tokens),
    )
    actions = (await control_plane_session.execute(select(AuditLog.action))).scalars().all()
    assert "staff_create" in actions and "login" in actions
