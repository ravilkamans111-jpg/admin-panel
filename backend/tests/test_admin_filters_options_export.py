"""Multi-value / date-range / virtual filters, label options for pickers,
CSV export and secret masking in the generic admin engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.api.deps import CurrentUser, get_current_user
from app.main import app
from app.registry.admin_models import SENSITIVE_MASK, get_config
from app.services import generic_write_service

pytestmark = pytest.mark.asyncio


async def test_multi_value_filter_matches_any(authed_client: AsyncClient):
    one = await authed_client.get("/admin/transactions", params={"status": "SUCCESS"})
    both = await authed_client.get("/admin/transactions", params={"status": "SUCCESS,DECLINED"})
    assert one.json()["total"] == 2
    assert both.json()["total"] == 3


async def test_date_range_filter(authed_client: AsyncClient):
    today = datetime.now(UTC).date().isoformat()
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    inside = await authed_client.get("/admin/transactions", params={"date_create__gte": today, "date_create__lte": today})
    before = await authed_client.get("/admin/transactions", params={"date_create__lte": yesterday})
    assert inside.json()["total"] == 3
    assert before.json()["total"] == 0


async def test_date_filter_rejects_garbage_and_unknown_fields(authed_client: AsyncClient):
    assert (await authed_client.get("/admin/transactions", params={"date_create__gte": "nope"})).status_code == 400
    assert (await authed_client.get("/admin/transactions", params={"amount__gte": "1"})).status_code == 400


async def test_virtual_partner_filter(authed_client: AsyncClient):
    companies = (await authed_client.get("/admin/companies")).json()["items"]
    partner_id = companies[0]["id"]
    assert (await authed_client.get("/admin/transactions", params={"company_id": partner_id})).json()["total"] == 3
    assert (await authed_client.get("/admin/transactions", params={"company_id": partner_id + 99})).json()["total"] == 0


async def test_filter_descriptors(authed_client: AsyncClient):
    body = (await authed_client.get("/admin/transactions/filter-options")).json()
    by_field = {d["field"]: d for d in body}
    assert by_field["status"]["kind"] == "choice"
    assert {o["value"] for o in by_field["status"]["options"]} == {"SUCCESS", "DECLINED"}
    assert by_field["merchant_id"] == {"field": "merchant_id", "kind": "fk", "target": "merchants"}
    assert by_field["company_id"]["target"] == "companies"
    assert by_field["date_create"]["kind"] == "date"


async def test_options_label_search_and_id_lookup(authed_client: AsyncClient):
    merchants = (await authed_client.get("/admin/merchants/options")).json()
    assert [m["label"] for m in merchants] == ["demo: Acme Merchant"]
    found = (await authed_client.get("/admin/merchants/options", params={"search": "DEMO"})).json()
    assert len(found) == 1
    assert (await authed_client.get("/admin/merchants/options", params={"search": "zzz"})).json() == []
    by_id = (await authed_client.get("/admin/merchants/options", params={"ids": str(merchants[0]["id"])})).json()
    assert by_id == merchants


async def test_options_for_composite_partner_method_label(authed_client: AsyncClient):
    options = (await authed_client.get("/admin/payment-method-companies/options")).json()
    assert [o["label"] for o in options] == ["[1] IN: tok1 — Acme Partner"]


async def test_options_rejects_model_without_label_template(authed_client: AsyncClient):
    assert (await authed_client.get("/admin/transactions/options")).status_code == 400


async def test_csv_export_uses_names_and_respects_filters(authed_client: AsyncClient):
    resp = await authed_client.get("/admin/transactions/export", params={"status": "SUCCESS"})
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment")
    text = resp.content.decode("utf-8")
    assert text.startswith("﻿")
    lines = text.lstrip("﻿").strip().splitlines()
    header = lines[0].split(";")
    assert len(lines) == 3  # header + 2 SUCCESS rows
    row = dict(zip(header, lines[1].split(";"), strict=True))
    assert row["merchant_id"] == "demo: Acme Merchant"
    assert row["payment_method_company_id"] == "Acme Partner"


async def test_export_bad_filter_is_400(authed_client: AsyncClient):
    assert (await authed_client.get("/admin/transactions/export", params={"date_create__gte": "x"})).status_code == 400


async def _as_role(role: str):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(admin_user_id=1, brand_id="ampay", role=role)


async def test_private_key_masked_below_brand_admin(authed_client: AsyncClient):
    listed = (await authed_client.get("/admin/merchants")).json()["items"][0]
    assert listed["private_key"] == SENSITIVE_MASK
    assert listed["public_key"] == "pub123"
    detail = (await authed_client.get(f"/admin/merchants/{listed['id']}")).json()
    assert detail["private_key"] == SENSITIVE_MASK
    assert SENSITIVE_MASK in (await authed_client.get("/admin/merchants/export")).text

    await _as_role("brand_admin")
    assert (await authed_client.get(f"/admin/merchants/{listed['id']}")).json()["private_key"] == "priv123"


async def test_masked_placeholder_does_not_overwrite_secret(tenant_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    config = get_config("merchants")
    async with async_sessionmaker(tenant_engine, expire_on_commit=False)() as session:
        merchant_id = (await session.execute(__import__("sqlalchemy").text("select id from merchant"))).scalar_one()
        _, after = await generic_write_service.update_record(
            session, config=config, pk=merchant_id, values={"private_key": SENSITIVE_MASK, "name": "Renamed"}
        )
    assert after["name"] == "Renamed"
    assert after["private_key"] == "priv123"


async def test_generic_create_cannot_bypass_cascade_item_validation(authed_client: AsyncClient, tenant_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.api.deps import get_tenant_write_session

    factory = async_sessionmaker(tenant_engine, expire_on_commit=False)

    async def write_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_tenant_write_session] = write_session
    await _as_role("operator")
    resp = await authed_client.post(
        "/admin/payment-method-cascade-items",
        json={"cascade_id": "1", "payment_method_company_id": "1", "priority": "1"},
    )
    assert resp.status_code == 400

    app.dependency_overrides.pop(get_tenant_write_session, None)
