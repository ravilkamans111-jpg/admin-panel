"""Unit tests for the ported balance-mutation logic in `app.services.balance_math`.

These exercise the actual money math against a real (SQLite, in-memory)
database — not mocks — since the functions under test issue real SELECTs
(`get_or_create`, `with_for_update`) as part of their logic. Coverage
focuses on the most common real-world transitions (new transaction, and
ACCEPTED -> SUCCESS / ACCEPTED -> DECLINED edits) for both directions; the
full branch matrix in the source is large (~15+ branches per function) and
not exhaustively covered here — see the module docstring in `balance_math.py`
for the branches these tests do NOT cover.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
from app.services.balance_math import (
    apply_company_balance_update,
    apply_merchant_balance_update,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        yield s
    await engine.dispose()


async def _seed(session: AsyncSession, *, currency_id_offset: int = 0) -> dict:
    user = DjangoAuthUser(
        username="demo", email="demo@example.com", is_active=True, is_staff=False,
        is_superuser=False, date_joined=datetime.now(UTC).isoformat(),
    )
    session.add(user)
    await session.flush()

    merchant = Merchant(name="Test Merchant", user_id=user.id)
    session.add(merchant)
    await session.flush()

    currency = Currency(iso_code="USD", addition_name="US Dollar", limit=0)
    session.add(currency)
    await session.flush()

    company = Company(name="TestPartner")
    session.add(company)
    await session.flush()

    payment_method = PaymentMethod(name="Card", direction="IN", token="tok1", currency_id=currency.id)
    session.add(payment_method)
    await session.flush()

    pmc = PaymentMethodCompany(
        is_active=True, partner_rate=Decimal("2.0"), company_id=company.id,
        payment_method_id=payment_method.id, last_reset=datetime.now(UTC),
    )
    session.add(pmc)
    await session.flush()

    return {
        "user": user, "merchant": merchant, "currency": currency,
        "company": company, "payment_method": payment_method, "pmc": pmc,
    }


def _make_transaction(*, merchant_id: int, pmc_id: int, direction: str, status: str, **overrides) -> Transaction:
    defaults = {
        "tracker_id": "trk1", "partner_system_id": "p1", "merchant_system_id": "m1",
        "status": status, "direction": direction,
        "date_create": datetime.now(UTC), "date_update": datetime.now(UTC),
        "amount": Decimal("100.00"), "commission": Decimal("5.00"),
        "partner_income": Decimal("2.00"), "pure_our_income": Decimal("3.00"),
        "amount_after_commission": Decimal("95.00"),
        "merchant_id": merchant_id, "payment_method_company_id": pmc_id,
    }
    defaults.update(overrides)
    return Transaction(**defaults)


# ---------------------------------------------------------------------------
# MerchantBalanceService.merchant_balance_update
# ---------------------------------------------------------------------------


async def test_merchant_balance_new_transaction_in_accepted(session: AsyncSession):
    ctx = await _seed(session)
    txn = _make_transaction(
        merchant_id=ctx["merchant"].id, pmc_id=ctx["pmc"].id, direction="IN", status="ACCEPTED"
    )
    session.add(txn)
    await session.flush()

    balance = await apply_merchant_balance_update(
        session, txn=txn, new_status="ACCEPTED", old_status=None, old_amount_after_commission=None
    )

    assert balance.blocked_balance_in == Decimal("95.00")
    assert balance.balance == Decimal("0.00")


async def test_merchant_balance_in_accepted_to_success(session: AsyncSession):
    ctx = await _seed(session)
    txn = _make_transaction(
        merchant_id=ctx["merchant"].id, pmc_id=ctx["pmc"].id, direction="IN", status="ACCEPTED"
    )
    session.add(txn)
    await session.flush()
    await apply_merchant_balance_update(
        session, txn=txn, new_status="ACCEPTED", old_status=None, old_amount_after_commission=None
    )

    txn.status = "SUCCESS"
    balance = await apply_merchant_balance_update(
        session, txn=txn, new_status="SUCCESS", old_status="ACCEPTED",
        old_amount_after_commission=Decimal("95.00"),
    )

    # Moves from blocked_balance_in into balance, no double counting.
    assert balance.blocked_balance_in == Decimal("0.00")
    assert balance.balance == Decimal("95.00")


async def test_merchant_balance_in_accepted_to_declined_refunds_block(session: AsyncSession):
    ctx = await _seed(session)
    txn = _make_transaction(
        merchant_id=ctx["merchant"].id, pmc_id=ctx["pmc"].id, direction="IN", status="ACCEPTED"
    )
    session.add(txn)
    await session.flush()
    await apply_merchant_balance_update(
        session, txn=txn, new_status="ACCEPTED", old_status=None, old_amount_after_commission=None
    )

    txn.status = "DECLINED"
    balance = await apply_merchant_balance_update(
        session, txn=txn, new_status="DECLINED", old_status="ACCEPTED",
        old_amount_after_commission=Decimal("95.00"),
    )

    assert balance.blocked_balance_in == Decimal("0.00")
    assert balance.balance == Decimal("0.00")


async def test_merchant_balance_new_transaction_out_accepted(session: AsyncSession):
    ctx = await _seed(session)
    txn = _make_transaction(
        merchant_id=ctx["merchant"].id, pmc_id=ctx["pmc"].id, direction="OUT", status="ACCEPTED"
    )
    session.add(txn)
    await session.flush()

    balance = await apply_merchant_balance_update(
        session, txn=txn, new_status="ACCEPTED", old_status=None, old_amount_after_commission=None
    )

    assert balance.blocked_balance_out == Decimal("95.00")
    assert balance.balance == Decimal("-95.00")


async def test_merchant_balance_appeal_status_is_a_noop(session: AsyncSession):
    """Source has no branch for APPEAL — matches bug-for-bug."""
    ctx = await _seed(session)
    txn = _make_transaction(
        merchant_id=ctx["merchant"].id, pmc_id=ctx["pmc"].id, direction="IN", status="APPEAL"
    )
    session.add(txn)
    await session.flush()

    balance = await apply_merchant_balance_update(
        session, txn=txn, new_status="APPEAL", old_status=None, old_amount_after_commission=None
    )

    assert balance.balance == Decimal("0.00")
    assert balance.blocked_balance_in == Decimal("0.00")


# ---------------------------------------------------------------------------
# CompanyService.update_company_balance
# ---------------------------------------------------------------------------


async def test_company_balance_new_transaction_in_accepted(session: AsyncSession):
    ctx = await _seed(session)
    txn = _make_transaction(
        merchant_id=ctx["merchant"].id, pmc_id=ctx["pmc"].id, direction="IN", status="ACCEPTED"
    )
    session.add(txn)
    await session.flush()

    balance = await apply_company_balance_update(session, txn=txn, old_status=None)

    # blocked_balance_in += amount - partner_income = 100 - 2 = 98
    assert balance.blocked_balance_in == Decimal("98.00")


async def test_company_balance_in_accepted_to_success(session: AsyncSession):
    ctx = await _seed(session)
    txn = _make_transaction(
        merchant_id=ctx["merchant"].id, pmc_id=ctx["pmc"].id, direction="IN", status="ACCEPTED"
    )
    session.add(txn)
    await session.flush()
    await apply_company_balance_update(session, txn=txn, old_status=None)

    txn.status = "SUCCESS"
    balance = await apply_company_balance_update(
        session, txn=txn, old_status="ACCEPTED",
        old_amount=Decimal("100.00"), old_amount_after_commission=Decimal("95.00"),
        old_our_income=Decimal("3.00"), old_partner_income=Decimal("2.00"),
    )

    assert balance.blocked_balance_in == Decimal("0.00")
    assert balance.available_balance == Decimal("98.00")  # amount - partner_income
    assert balance.our_income == Decimal("3.00")
    assert balance.clients_funds == Decimal("95.00")


async def test_company_balance_new_transaction_out_accepted(session: AsyncSession):
    ctx = await _seed(session)
    txn = _make_transaction(
        merchant_id=ctx["merchant"].id, pmc_id=ctx["pmc"].id, direction="OUT", status="ACCEPTED"
    )
    session.add(txn)
    await session.flush()

    balance = await apply_company_balance_update(session, txn=txn, old_status=None)

    assert balance.blocked_balance_out == Decimal("100.00")
    assert balance.available_balance == Decimal("-102.00")  # -(amount + partner_income)
    assert balance.clients_funds == Decimal("-95.00")


async def test_company_balance_declined_status_untouched_no_old_status(session: AsyncSession):
    """Source's DECLINED branch only fires when old_status is ACCEPTED/SUCCESS —
    a brand-new DECLINED transaction (no old_status) touches nothing."""
    ctx = await _seed(session)
    txn = _make_transaction(
        merchant_id=ctx["merchant"].id, pmc_id=ctx["pmc"].id, direction="IN", status="DECLINED"
    )
    session.add(txn)
    await session.flush()

    balance = await apply_company_balance_update(session, txn=txn, old_status=None)

    assert balance.blocked_balance_in == Decimal("0.00")
    assert balance.available_balance == Decimal("0.00")
