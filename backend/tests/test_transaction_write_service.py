"""Integration test for the full Transaction edit orchestration —
`app.services.transaction_write_service.update_transaction` — covering the
general branch (not admin-merchant, not ampay+settlement): both merchant
and company balances get updated in one call, on top of the field edit
itself.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("USE_LOCAL_ENV_SECRETS", "true")

from app.models.tenant import (
    Company,
    Currency,
    DjangoAuthUser,
    Merchant,
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
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        yield s
    await engine.dispose()


async def test_update_transaction_runs_both_balance_updates(session: AsyncSession, monkeypatch):
    # Not the admin-merchant branch and not "ampay"+settlement — exercises
    # the general (most common) path: merchant AND company balance both update.
    monkeypatch.setattr(
        "app.services.transaction_write_service.get_brand_admin_public_key", lambda brand_id: None
    )

    user = DjangoAuthUser(
        username="demo", email="demo@example.com", is_active=True, is_staff=False,
        is_superuser=False, date_joined=datetime.now(UTC).isoformat(),
    )
    session.add(user)
    await session.flush()
    merchant = Merchant(name="M", user_id=user.id)
    session.add(merchant)
    await session.flush()
    currency = Currency(iso_code="USD", addition_name="US Dollar", limit=0)
    session.add(currency)
    await session.flush()
    company = Company(name="NotAmpay")
    session.add(company)
    await session.flush()
    payment_method = PaymentMethod(name="Card", direction="IN", token="tokX", currency_id=currency.id)
    session.add(payment_method)
    await session.flush()
    pmc = PaymentMethodCompany(
        is_active=True, partner_rate=Decimal("2.0"), company_id=company.id,
        payment_method_id=payment_method.id, last_reset=datetime.now(UTC),
    )
    session.add(pmc)
    await session.flush()
    txn = Transaction(
        tracker_id="trk1", partner_system_id="p1", merchant_system_id="m1",
        status="ACCEPTED", direction="IN",
        date_create=datetime.now(UTC), date_update=datetime.now(UTC),
        amount=Decimal("100.00"), commission=Decimal("5.00"),
        partner_income=Decimal("2.00"), pure_our_income=Decimal("3.00"),
        amount_after_commission=Decimal("95.00"),
        merchant_id=merchant.id, payment_method_company_id=pmc.id,
    )
    session.add(txn)
    await session.flush()

    before, after = await transaction_write_service.update_transaction(
        session, brand_id="ampay", pk=txn.id, values={"status": "SUCCESS"}
    )

    assert before["status"] == "ACCEPTED"
    assert after["status"] == "SUCCESS"

    from sqlalchemy import select

    from app.models.tenant import CompanyBalance, MerchantBalance

    merchant_balance = (
        await session.execute(select(MerchantBalance).where(MerchantBalance.merchant_id == merchant.id))
    ).scalar_one()
    company_balance = (
        await session.execute(select(CompanyBalance).where(CompanyBalance.company_id == company.id))
    ).scalar_one()

    # ACCEPTED (never actually applied, since we jumped straight to SUCCESS
    # with old_status="ACCEPTED") -> SUCCESS transition on IN:
    # merchant.balance += amount_after_commission (95), blocked_balance_in -= 95 (was never set, so goes negative:
    # this specific fixture skips the initial ACCEPTED apply, so blocked_balance_in starts at 0 and the
    # transition still fires the "old_status == ACCEPTED" branch per old_snapshot.status, which is what we're
    # testing: the write service correctly threads old_status/old_amount_after_commission through.
    assert merchant_balance.balance == Decimal("95.00")
    assert company_balance.available_balance == Decimal("98.00")  # amount - partner_income
    assert company_balance.our_income == Decimal("3.00")


async def test_update_transaction_raises_on_unknown_field(session: AsyncSession):
    user = DjangoAuthUser(
        username="demo2", email="demo2@example.com", is_active=True, is_staff=False,
        is_superuser=False, date_joined=datetime.now(UTC).isoformat(),
    )
    session.add(user)
    await session.flush()
    merchant = Merchant(name="M2", user_id=user.id)
    session.add(merchant)
    await session.flush()
    currency = Currency(iso_code="EUR", addition_name="Euro", limit=0)
    session.add(currency)
    await session.flush()
    company = Company(name="Partner2")
    session.add(company)
    await session.flush()
    payment_method = PaymentMethod(name="Card", direction="IN", token="tokY", currency_id=currency.id)
    session.add(payment_method)
    await session.flush()
    pmc = PaymentMethodCompany(
        is_active=True, partner_rate=Decimal("2.0"), company_id=company.id,
        payment_method_id=payment_method.id, last_reset=datetime.now(UTC),
    )
    session.add(pmc)
    await session.flush()
    txn = Transaction(
        tracker_id="trk2", partner_system_id="p2", merchant_system_id="m2",
        status="ACCEPTED", direction="IN",
        date_create=datetime.now(UTC), date_update=datetime.now(UTC),
        amount=Decimal("10.00"), commission=Decimal("1.00"),
        partner_income=Decimal("0.50"), pure_our_income=Decimal("0.50"),
        amount_after_commission=Decimal("9.00"),
        merchant_id=merchant.id, payment_method_company_id=pmc.id,
    )
    session.add(txn)
    await session.flush()

    import pytest

    with pytest.raises(ValueError, match="[Nn]ot editable"):
        await transaction_write_service.update_transaction(
            session, brand_id="ampay", pk=txn.id, values={"merchant_id": 999}
        )
