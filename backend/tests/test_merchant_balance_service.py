"""Unit tests for `app.services.merchant_balance_service` — the raw-SQL
recompute ported from `MerchantBalanceAdmin.refresh_balances`.

Runs against SQLite (like every other test here) rather than Postgres —
`UPDATE ... FROM (subquery)` is supported by SQLite 3.33+ with the same
semantics needed here, so the ported SQL runs unmodified. This exercises
the actual query, not a Python re-implementation of it.
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
    MerchantBalance,
    PaymentMethod,
    PaymentMethodCompany,
    TenantBase,
    Transaction,
)
from app.services import merchant_balance_service


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        yield s
    await engine.dispose()


async def _seed_merchant(session: AsyncSession, name: str) -> Merchant:
    user = DjangoAuthUser(
        username=f"user-{name}", email=f"{name}@example.com", is_active=True, is_staff=False,
        is_superuser=False, date_joined=datetime.now(UTC).isoformat(),
    )
    session.add(user)
    await session.flush()
    merchant = Merchant(name=name, user_id=user.id)
    session.add(merchant)
    await session.flush()
    return merchant


async def _seed_pmc(session: AsyncSession, currency: Currency) -> PaymentMethodCompany:
    company = Company(name=f"Partner-{currency.id}")
    session.add(company)
    pm = PaymentMethod(name="Card", direction="IN", token=f"tok-{currency.id}", currency_id=currency.id)
    session.add(pm)
    await session.flush()
    pmc = PaymentMethodCompany(
        is_active=True, partner_rate=0, additional_commission=0, settlement_commission=0,
        daily_amount_limit=None, daily_count_limit=None, transaction_min_limit=None,
        transaction_max_limit=None, current_daily_amount=0, current_daily_amount_success=0,
        current_daily_count=0, current_daily_coun_success=0, last_reset=datetime.now(UTC),
        changing_rate=False, priority=1, company_id=company.id, payment_method_id=pm.id,
    )
    session.add(pmc)
    await session.flush()
    return pmc


_txn_counter = 0


def _txn(*, merchant_id, pmc_id, direction, status, amount_after_commission) -> Transaction:
    global _txn_counter
    _txn_counter += 1
    now = datetime.now(UTC)
    return Transaction(
        tracker_id=f"tracker-{_txn_counter}",
        partner_system_id=f"partner-{_txn_counter}",
        merchant_system_id=f"merchant-sys-{_txn_counter}",
        merchant_id=merchant_id,
        payment_method_company_id=pmc_id,
        direction=direction,
        status=status,
        amount=amount_after_commission,
        amount_after_commission=amount_after_commission,
        commission=Decimal(0),
        partner_income=Decimal(0),
        pure_our_income=Decimal(0),
        date_create=now,
        date_update=now,
    )


async def test_refresh_recomputes_balance_from_ledger(session: AsyncSession):
    currency = Currency(iso_code="USD", addition_name="USD", limit=0)
    session.add(currency)
    await session.flush()
    merchant = await _seed_merchant(session, "m1")
    pmc = await _seed_pmc(session, currency)

    session.add_all(
        [
            _txn(merchant_id=merchant.id, pmc_id=pmc.id, direction="IN", status="SUCCESS", amount_after_commission=Decimal("100.00")),
            _txn(merchant_id=merchant.id, pmc_id=pmc.id, direction="OUT", status="SUCCESS", amount_after_commission=Decimal("30.00")),
            _txn(merchant_id=merchant.id, pmc_id=pmc.id, direction="OUT", status="ACCEPTED", amount_after_commission=Decimal("10.00")),
            _txn(merchant_id=merchant.id, pmc_id=pmc.id, direction="IN", status="ACCEPTED", amount_after_commission=Decimal("5.00")),
            _txn(merchant_id=merchant.id, pmc_id=pmc.id, direction="IN", status="DECLINED", amount_after_commission=Decimal("999.00")),
        ]
    )
    balance = MerchantBalance(
        balance=Decimal(0), blocked_balance_in=Decimal(0), blocked_balance_out=Decimal(0),
        blocked_balance_usdt_in=Decimal(0), blocked_balance_usdt_out=Decimal(0), balance_usdt=Decimal(0),
        currency_id=currency.id, merchant_id=merchant.id,
    )
    session.add(balance)
    await session.flush()

    updated = await merchant_balance_service.refresh_all_merchant_balances(session)
    await session.refresh(balance)

    assert updated == 1
    # balance = 100 (IN success) - 30 (OUT success) - 10 (OUT accepted) = 60
    assert balance.balance == Decimal("60.00")
    assert balance.blocked_balance_in == Decimal("5.00")
    assert balance.blocked_balance_out == Decimal("10.00")


async def test_refresh_leaves_balance_row_untouched_when_no_transactions_exist(session: AsyncSession):
    """Preserved quirk: a MerchantBalance row for a (merchant, currency) pair
    with NO transactions at all is not matched by the `calc` join and is
    left completely alone — not reset to zero."""
    currency = Currency(iso_code="EUR", addition_name="EUR", limit=0)
    session.add(currency)
    await session.flush()
    merchant = await _seed_merchant(session, "m2")

    balance = MerchantBalance(
        balance=Decimal("42.00"), blocked_balance_in=Decimal("7.00"), blocked_balance_out=Decimal("3.00"),
        blocked_balance_usdt_in=Decimal(0), blocked_balance_usdt_out=Decimal(0), balance_usdt=Decimal(0),
        currency_id=currency.id, merchant_id=merchant.id,
    )
    session.add(balance)
    await session.flush()

    updated = await merchant_balance_service.refresh_all_merchant_balances(session)
    await session.refresh(balance)

    assert updated == 0
    assert balance.balance == Decimal("42.00")
    assert balance.blocked_balance_in == Decimal("7.00")
    assert balance.blocked_balance_out == Decimal("3.00")


async def test_refresh_does_not_create_missing_balance_rows(session: AsyncSession):
    """Preserved quirk: a (merchant, currency) pair with real transactions
    but no pre-existing MerchantBalance row gets no row created — this is
    an UPDATE, not an upsert."""
    currency = Currency(iso_code="GBP", addition_name="GBP", limit=0)
    session.add(currency)
    await session.flush()
    merchant = await _seed_merchant(session, "m3")
    pmc = await _seed_pmc(session, currency)
    session.add(_txn(merchant_id=merchant.id, pmc_id=pmc.id, direction="IN", status="SUCCESS", amount_after_commission=Decimal("50.00")))
    await session.flush()

    updated = await merchant_balance_service.refresh_all_merchant_balances(session)

    assert updated == 0
    result = await session.execute(
        MerchantBalance.__table__.select().where(MerchantBalance.merchant_id == merchant.id)
    )
    assert result.first() is None
