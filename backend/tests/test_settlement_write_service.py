"""Integration tests for `app.services.settlement_write_service` — covering
the TO_MERCHANT settlement type (the common "pay out to a merchant from
company reserve" case): creates a linked Transaction, overrides
`balance_partner` to the "AmPay" company's balance regardless of what was
submitted, and runs the same balance side effects the Transaction write
path uses.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("USE_LOCAL_ENV_SECRETS", "true")

from app.models.tenant import (
    Company,
    CompanyBalance,
    Currency,
    DjangoAuthUser,
    Merchant,
    MerchantBalance,
    PaymentMethod,
    PaymentMethodCompany,
    TenantBase,
)
from app.services import settlement_write_service


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        yield s
    await engine.dispose()


async def _seed_to_merchant_scenario(session: AsyncSession) -> dict:
    user = DjangoAuthUser(
        username="demo", email="demo@example.com", is_active=True, is_staff=False,
        is_superuser=False, date_joined=datetime.now(UTC).isoformat(),
    )
    session.add(user)
    await session.flush()

    merchant = Merchant(name="Payout Merchant", user_id=user.id)
    session.add(merchant)
    await session.flush()

    currency = Currency(iso_code="USD", addition_name="US Dollar", limit=0)
    session.add(currency)
    await session.flush()

    merchant_balance = MerchantBalance(merchant_id=merchant.id, currency_id=currency.id, balance=Decimal("500.00"))
    session.add(merchant_balance)
    await session.flush()

    # Pre-existing "AmPay" company — get_or_create_by_name must find this,
    # not create a duplicate.
    ampay = Company(name="AmPay")
    session.add(ampay)
    await session.flush()

    settlement_method = PaymentMethod(
        name="settlement", direction="OUT", token="settle-out-usd", currency_id=currency.id
    )
    session.add(settlement_method)
    await session.flush()

    ampay_pmc = PaymentMethodCompany(
        is_active=True, partner_rate=Decimal(0), company_id=ampay.id,
        payment_method_id=settlement_method.id, last_reset=datetime.now(UTC),
    )
    session.add(ampay_pmc)
    await session.flush()

    # Placeholder balance_partner the caller "selects" — must be overridden.
    other_company = Company(name="SomeOtherPartner")
    session.add(other_company)
    await session.flush()
    placeholder_balance = CompanyBalance(company_id=other_company.id, currency_id=currency.id)
    session.add(placeholder_balance)
    await session.flush()

    return {
        "merchant": merchant, "currency": currency, "merchant_balance": merchant_balance,
        "ampay": ampay, "placeholder_balance": placeholder_balance,
    }


async def test_create_to_merchant_settlement_overrides_balance_partner_and_pays_merchant(session: AsyncSession):
    ctx = await _seed_to_merchant_scenario(session)

    result = await settlement_write_service.create_or_update_settlement(
        session,
        brand_id="ampay",
        pk=None,
        values={
            "settl_type": "TO_MERCHANT",
            "balance_merchant_id": ctx["merchant_balance"].id,
            "balance_partner_id": ctx["placeholder_balance"].id,
            "amount": Decimal("100.00"),
            "commission": Decimal(5),  # 5%
        },
    )

    assert result.settlement_after["settl_type"] == "TO_MERCHANT"
    # balance_partner must have been overridden to AmPay's balance, not the
    # placeholder the caller submitted.
    assert result.settlement_after["balance_partner_id"] != ctx["placeholder_balance"].id

    ampay_balance_result = await session.execute(
        select(CompanyBalance).where(CompanyBalance.company_id == ctx["ampay"].id)
    )
    ampay_balance = ampay_balance_result.scalar_one()
    assert result.settlement_after["balance_partner_id"] == ampay_balance.id

    # Source's `pre_save_from_admin_settlement` OVERWRITES the commission-
    # discounted final_amount back to the full `amount` for TO_MERCHANT
    # specifically (verbatim source behavior — see the code comment in
    # settlement_write_service.py) — commission is computed and fed into
    # the linked Transaction's commission/partner_income/pure_our_income,
    # but never actually subtracted from final_amount here.
    assert Decimal(result.settlement_after["final_amount"]) == Decimal("100.00")

    # Linked transaction: direction OUT (TO_MERCHANT), amount 100,
    # amount_after_commission == amount (same override applies).
    assert result.transaction_after["direction"] == "OUT"
    assert Decimal(result.transaction_after["amount"]) == Decimal("100.00")
    assert Decimal(result.transaction_after["amount_after_commission"]) == Decimal("100.00")
    assert Decimal(result.transaction_after["commission"]) == Decimal("5.00")

    # settlement.transaction_id now points at the newly created Transaction.
    assert result.settlement_after["transaction_id"] == result.transaction_after["tracker_id"]

    # Merchant balance side effect: general-branch would run since the
    # transaction's own company ("AmPay") + settlement method hits the
    # "ampay + settlement" branch -> ONLY merchant_balance_update runs
    # (matches source's TransactionSaveService branch for this exact case).
    await session.refresh(ctx["merchant_balance"])
    # New OUT transaction, status ACCEPTED (settlement default) -> blocked_balance_out
    # increases by amount_after_commission (== amount here, per the override above).
    assert ctx["merchant_balance"].blocked_balance_out == Decimal("100.00")
    assert ctx["merchant_balance"].balance == Decimal("500.00") - Decimal("100.00")


async def test_update_settlement_rejects_unknown_field(session: AsyncSession):
    ctx = await _seed_to_merchant_scenario(session)
    result = await settlement_write_service.create_or_update_settlement(
        session,
        brand_id="ampay",
        pk=None,
        values={
            "settl_type": "TO_MERCHANT",
            "balance_merchant_id": ctx["merchant_balance"].id,
            "balance_partner_id": ctx["placeholder_balance"].id,
            "amount": Decimal("10.00"),
        },
    )
    settlement_id = result.settlement_after["id"]

    with pytest.raises(ValueError, match="[Nn]ot editable"):
        await settlement_write_service.create_or_update_settlement(
            session, brand_id="ampay", pk=settlement_id, values={"settl_type": "FROM_MERCHANT"}
        )


async def test_create_settlement_requires_create_only_fields(session: AsyncSession):
    with pytest.raises(ValueError, match="Required on create"):
        await settlement_write_service.create_or_update_settlement(
            session, brand_id="ampay", pk=None, values={"amount": Decimal("10.00")}
        )
