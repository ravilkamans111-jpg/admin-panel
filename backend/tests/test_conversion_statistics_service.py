"""Conversion-statistics side effect of a Transaction edit
(`app.services.conversion_statistics_service`) — including the cases where
source silently writes nothing."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("USE_LOCAL_ENV_SECRETS", "true")

from app.models.tenant import (
    Company,
    ConversionStatisticsMerchantNew,
    ConversionStatisticsNew,
    ConversionStatisticsPartnersNew,
    Currency,
    DjangoAuthUser,
    Merchant,
    MerchantPaymentMethod,
    PaymentMethod,
    PaymentMethodCompany,
    TenantBase,
    Transaction,
)
from app.services import transaction_write_service


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def _world(session: AsyncSession, monkeypatch, *, txn_date: datetime, status="ACCEPTED", amount="100.00"):
    monkeypatch.setattr("app.services.transaction_write_service.get_brand_admin_public_key", lambda b: None)
    user = DjangoAuthUser(
        username="demo", email="d@example.com", is_active=True, is_staff=False,
        is_superuser=False, date_joined=datetime.now(UTC).isoformat(),
    )
    session.add(user)
    await session.flush()
    merchant = Merchant(name="M", user_id=user.id)
    currency = Currency(iso_code="USD", addition_name="US Dollar", limit=0)
    company = Company(name="Partner")
    session.add_all([merchant, currency, company])
    await session.flush()
    pm = PaymentMethod(name="Card", direction="IN", token="t", currency_id=currency.id)
    session.add(pm)
    await session.flush()
    pmc = PaymentMethodCompany(
        is_active=True, partner_rate=Decimal("2.0"), company_id=company.id,
        payment_method_id=pm.id, last_reset=datetime.now(UTC),
    )
    mpm = MerchantPaymentMethod(merchant_id=merchant.id, payment_method_id=pm.id, personal_rate=Decimal(5))
    session.add_all([pmc, mpm])
    await session.flush()
    txn = Transaction(
        tracker_id="t1", partner_system_id="p", merchant_system_id="m", status=status, direction="IN",
        date_create=txn_date, date_update=txn_date, amount=Decimal(amount), commission=Decimal("5.00"),
        partner_income=Decimal("2.00"), pure_our_income=Decimal("3.00"),
        amount_after_commission=Decimal("95.00"), merchant_id=merchant.id, payment_method_company_id=pmc.id,
    )
    session.add(txn)
    await session.flush()
    return txn, pm, pmc, mpm


def _stat(model, fk, fk_value, when: datetime, **counters):
    base = {
        "num_requests": 0, "num_requests_success": 0, "num_paid_orders": 0, "amount_requests": Decimal(0),
        "amount_requests_success": Decimal(0), "amount_paid_orders": Decimal(0), "conversion_percent": Decimal(0),
        "conversion_percent_paid_orders": Decimal(0), "date_create": when, "data_update": when,
        "date_only": when.date(),
    }
    return model(**{fk: fk_value}, **{**base, **counters})


async def _seed_rows(session, pm, pmc, mpm, when, **counters):
    session.add_all([
        _stat(ConversionStatisticsNew, "payment_method_id", pm.id, when, **counters),
        _stat(ConversionStatisticsMerchantNew, "merchant_payment_method_id", mpm.id, when, **counters),
        _stat(ConversionStatisticsPartnersNew, "payment_method_company_id", pmc.id, when, **counters),
    ])
    await session.flush()


async def test_accepted_to_success_counts_paid_order_on_all_three_rollups(session, monkeypatch):
    now = datetime.now(UTC)
    txn, pm, pmc, mpm = await _world(session, monkeypatch, txn_date=now)
    await _seed_rows(session, pm, pmc, mpm, now, num_requests=4, num_requests_success=2,
                     amount_requests=Decimal(400), amount_requests_success=Decimal(200))

    await transaction_write_service.update_transaction(session, brand_id="ampay", pk=txn.id, values={"status": "SUCCESS"})

    for model in (ConversionStatisticsNew, ConversionStatisticsMerchantNew, ConversionStatisticsPartnersNew):
        stat = (await session.execute(select(model))).scalar_one()
        assert stat.num_paid_orders == 1
        assert stat.amount_paid_orders == Decimal("100.0000")
        assert stat.conversion_percent == Decimal("50.00")  # 2 / 4
        assert stat.conversion_percent_paid_orders == Decimal("50.00")  # 100 / 200


async def test_success_to_declined_reverses_paid_order(session, monkeypatch):
    now = datetime.now(UTC)
    txn, pm, pmc, mpm = await _world(session, monkeypatch, txn_date=now, status="SUCCESS")
    await _seed_rows(session, pm, pmc, mpm, now, num_requests=2, num_requests_success=2, num_paid_orders=1,
                     amount_requests=Decimal(200), amount_requests_success=Decimal(200),
                     amount_paid_orders=Decimal(100))

    await transaction_write_service.update_transaction(session, brand_id="ampay", pk=txn.id, values={"status": "DECLINED"})

    stat = (await session.execute(select(ConversionStatisticsNew))).scalar_one()
    assert stat.num_paid_orders == 0
    assert stat.amount_paid_orders == Decimal(0)


async def test_success_amount_edit_adjusts_paid_amount(session, monkeypatch):
    now = datetime.now(UTC)
    txn, pm, pmc, mpm = await _world(session, monkeypatch, txn_date=now, status="SUCCESS")
    await _seed_rows(session, pm, pmc, mpm, now, num_requests=1, num_requests_success=1, num_paid_orders=1,
                     amount_requests=Decimal(100), amount_requests_success=Decimal(100),
                     amount_paid_orders=Decimal(100))

    await transaction_write_service.update_transaction(session, brand_id="ampay", pk=txn.id, values={"amount": Decimal("150.00")})

    stat = (await session.execute(select(ConversionStatisticsNew))).scalar_one()
    assert stat.num_paid_orders == 1
    assert stat.amount_paid_orders == Decimal("150.0000")


async def test_missing_row_is_created_today_but_old_transaction_date_aborts_all_stats(session, monkeypatch):
    now = datetime.now(UTC)
    txn, *_ = await _world(session, monkeypatch, txn_date=now - timedelta(days=3))

    await transaction_write_service.update_transaction(session, brand_id="ampay", pk=txn.id, values={"status": "SUCCESS"})

    # Source creates the row dated today, fails to re-read it by the old date and rolls everything back.
    for model in (ConversionStatisticsNew, ConversionStatisticsMerchantNew, ConversionStatisticsPartnersNew):
        assert (await session.execute(select(model))).scalars().all() == []
    # ...but the edit itself still stands.
    assert (await session.get(Transaction, txn.id)).status == "SUCCESS"


async def test_zero_requested_success_amount_skips_stats_but_keeps_edit(session, monkeypatch):
    now = datetime.now(UTC)
    txn, pm, pmc, mpm = await _world(session, monkeypatch, txn_date=now)
    await _seed_rows(session, pm, pmc, mpm, now)  # amount_requests_success == 0 → division by zero in source

    await transaction_write_service.update_transaction(session, brand_id="ampay", pk=txn.id, values={"status": "SUCCESS"})

    stat = (await session.execute(select(ConversionStatisticsNew))).scalar_one()
    assert stat.num_paid_orders == 0  # savepoint rolled back
    assert (await session.get(Transaction, txn.id)).status == "SUCCESS"
