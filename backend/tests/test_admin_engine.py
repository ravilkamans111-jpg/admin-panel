from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_schema_lists_all_registered_models(authed_client: AsyncClient):
    resp = await authed_client.get("/admin/schema")
    assert resp.status_code == 200
    keys = {c["key"] for c in resp.json()}
    assert "transactions" in keys
    assert "merchants" in keys
    assert "settlements" in keys
    assert len(keys) >= 25  # full read-only surface, not just the reference vertical


async def test_transactions_list_is_paginated_and_ordered(authed_client: AsyncClient):
    resp = await authed_client.get("/admin/transactions", params={"page_size": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["page_size"] == 2


async def test_transactions_filter_by_status(authed_client: AsyncClient):
    resp = await authed_client.get("/admin/transactions", params={"status": "SUCCESS"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(item["status"] == "SUCCESS" for item in body["items"])


async def test_transactions_search_by_tracker_id(authed_client: AsyncClient):
    resp = await authed_client.get("/admin/transactions", params={"search": "trk_1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["tracker_id"] == "trk_1"


async def test_unknown_filter_field_rejected(authed_client: AsyncClient):
    resp = await authed_client.get("/admin/transactions", params={"amount": "100.00"})
    assert resp.status_code == 400


async def test_retrieve_single_transaction(authed_client: AsyncClient):
    list_resp = await authed_client.get("/admin/transactions", params={"page_size": 1})
    tx_id = list_resp.json()["items"][0]["id"]

    resp = await authed_client.get(f"/admin/transactions/{tx_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == tx_id


async def test_retrieve_missing_record_404s(authed_client: AsyncClient):
    resp = await authed_client.get("/admin/transactions/999999")
    assert resp.status_code == 404


async def test_unknown_model_key_404s(authed_client: AsyncClient):
    resp = await authed_client.get("/admin/not-a-real-model")
    assert resp.status_code == 404


async def test_dashboard_summary(authed_client: AsyncClient):
    resp = await authed_client.get("/dashboard/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_transactions"] == 3
    assert body["transactions_by_status"]["SUCCESS"] == 2
    assert body["transactions_by_status"]["DECLINED"] == 1
