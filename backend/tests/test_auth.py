from __future__ import annotations

import pytest
from httpx import AsyncClient

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


# --- Hardcoded superuser (app.services.auth_service.HARDCODED_SUPERUSER_ID) ---
# No `admin_user` DB row exists for this identity anywhere in these tests —
# that absence IS the thing under test.


async def test_hardcoded_superuser_login_returns_all_known_brands(raw_client: AsyncClient, control_plane_session):
    from app.core.settings_env import env_settings

    resp = await raw_client.post(
        "/auth/login",
        json={"email": env_settings.hardcoded_superuser_email, "password": env_settings.hardcoded_superuser_password},
    )
    assert resp.status_code == 200
    body = resp.json()
    brand_ids = {b["brand_id"] for b in body["available_brands"]}
    assert brand_ids == {"ampay", "rajapay", "quiet-forest"}
    assert all(b["role"] == "superadmin" for b in body["available_brands"])


async def test_hardcoded_superuser_wrong_password_rejected(raw_client: AsyncClient, control_plane_session):
    from app.core.settings_env import env_settings

    resp = await raw_client.post(
        "/auth/login", json={"email": env_settings.hardcoded_superuser_email, "password": "not-the-password"}
    )
    assert resp.status_code == 401


async def test_hardcoded_superuser_select_brand_and_refresh_work_without_db_row(
    raw_client: AsyncClient, control_plane_session
):
    from app.core.settings_env import env_settings

    login_resp = await raw_client.post(
        "/auth/login",
        json={"email": env_settings.hardcoded_superuser_email, "password": env_settings.hardcoded_superuser_password},
    )
    pre_auth_token = login_resp.json()["pre_auth_token"]

    select_resp = await raw_client.post(
        "/auth/select-brand",
        json={"brand_id": "quiet-forest"},
        headers={"Authorization": f"Bearer {pre_auth_token}"},
    )
    assert select_resp.status_code == 200
    body = select_resp.json()
    assert body["role"] == "superadmin"
    assert body["brand_id"] == "quiet-forest"

    refresh_resp = await raw_client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["role"] == "superadmin"
