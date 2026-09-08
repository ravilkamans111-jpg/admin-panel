"""Unit tests for `app.services.cache_clear_actions_service` — the three
reachable cache-clear admin actions ported from `api_mediator/admin.py`.

Redis is mocked out (`safe_invalidate_cache` is patched to record calls)
— what's under test here is which currencies/merchants get resolved and
invalidated from a given selection, not the Redis wire protocol.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("USE_LOCAL_ENV_SECRETS", "true")

from app.core.exceptions import RecordNotFoundError
from app.models.tenant import (
    Currency,
    DjangoAuthUser,
    Merchant,
    MerchantPaymentMethod,
    PaymentMethod,
    PaymentMethodCompany,
    TenantBase,
)
from app.models.tenant.mediator import Company
from app.services import cache_clear_actions_service


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
def invalidation_calls(monkeypatch):
    calls: list[dict] = []

    async def _fake(brand_id: str, **kwargs):
        calls.append({"brand_id": brand_id, **kwargs})

    monkeypatch.setattr(cache_clear_actions_service, "safe_invalidate_cache", _fake)
    return calls


async def _seed_currency(session: AsyncSession, iso_code: str) -> Currency:
    currency = Currency(iso_code=iso_code, addition_name=iso_code, limit=0)
    session.add(currency)
    await session.flush()
    return currency


async def _seed_pmc(session: AsyncSession, currency: Currency, *, priority: int = 1) -> PaymentMethodCompany:
    company = Company(name=f"Partner-{currency.id}-{priority}")
    session.add(company)
    pm = PaymentMethod(name="Card", direction="IN", token=f"tok-{currency.id}-{priority}", currency_id=currency.id)
    session.add(pm)
    await session.flush()
    pmc = PaymentMethodCompany(
        is_active=True, partner_rate=0, additional_commission=0, settlement_commission=0,
        daily_amount_limit=None, daily_count_limit=None, transaction_min_limit=None,
        transaction_max_limit=None, current_daily_amount=0, current_daily_amount_success=0,
        current_daily_count=0, current_daily_coun_success=0, last_reset=datetime.now(UTC),
        changing_rate=False, priority=priority, company_id=company.id, payment_method_id=pm.id,
    )
    session.add(pmc)
    await session.flush()
    return pmc


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


async def _seed_mpm(session: AsyncSession, merchant: Merchant, pmc: PaymentMethodCompany) -> MerchantPaymentMethod:
    mpm = MerchantPaymentMethod(
        personal_rate=Decimal(10), additional_commission=0, test_mode=True, block=False, no_callback=False,
        transaction_min_limit=None, transaction_max_limit=None, only_admin_configure=False, cascade_id=None,
        merchant_id=merchant.id, payment_method_id=pmc.payment_method_id,
    )
    session.add(mpm)
    await session.flush()
    return mpm


# --- clear_cache_by_currency -------------------------------------------


async def test_clear_by_currency_invalidates_all_currencies_of_selected_rows(session, invalidation_calls):
    usd = await _seed_currency(session, "USD")
    eur = await _seed_currency(session, "EUR")
    pmc_usd = await _seed_pmc(session, usd)
    pmc_eur = await _seed_pmc(session, eur)

    result = await cache_clear_actions_service.clear_cache_by_currency(
        session, brand_id="ampay", payment_method_company_ids=[pmc_usd.id, pmc_eur.id]
    )

    assert result == ["EUR", "USD"]
    assert {c["currency"] for c in invalidation_calls} == {"EUR", "USD"}


async def test_clear_by_currency_deduplicates_same_currency(session, invalidation_calls):
    usd = await _seed_currency(session, "USD")
    pmc1 = await _seed_pmc(session, usd, priority=1)
    pmc2 = await _seed_pmc(session, usd, priority=2)

    result = await cache_clear_actions_service.clear_cache_by_currency(
        session, brand_id="ampay", payment_method_company_ids=[pmc1.id, pmc2.id]
    )

    assert result == ["USD"]
    assert len(invalidation_calls) == 1


async def test_clear_by_currency_rejects_empty_selection(session, invalidation_calls):
    with pytest.raises(ValueError, match="[Нн]е выбрано"):
        await cache_clear_actions_service.clear_cache_by_currency(session, brand_id="ampay", payment_method_company_ids=[])


async def test_clear_by_currency_rejects_unknown_ids(session, invalidation_calls):
    with pytest.raises(RecordNotFoundError):
        await cache_clear_actions_service.clear_cache_by_currency(session, brand_id="ampay", payment_method_company_ids=[999])


# --- clear_cache_by_merchant ---------------------------------------------


async def test_clear_by_merchant_invalidates_each_selected_merchant(session, invalidation_calls):
    currency = await _seed_currency(session, "USD")
    pmc = await _seed_pmc(session, currency)
    m1 = await _seed_merchant(session, "Zeta")
    m2 = await _seed_merchant(session, "Alpha")
    mpm1 = await _seed_mpm(session, m1, pmc)
    mpm2 = await _seed_mpm(session, m2, pmc)

    result = await cache_clear_actions_service.clear_cache_by_merchant(
        session, brand_id="ampay", merchant_payment_method_ids=[mpm1.id, mpm2.id]
    )

    assert [m["name"] for m in result] == ["Alpha", "Zeta"]  # sorted by name
    assert {c["merchant_id"] for c in invalidation_calls} == {m1.id, m2.id}


async def test_clear_by_merchant_rejects_empty_selection(session, invalidation_calls):
    with pytest.raises(ValueError, match="[Нн]е выбрано"):
        await cache_clear_actions_service.clear_cache_by_merchant(session, brand_id="ampay", merchant_payment_method_ids=[])


# --- clear_all_payment_methods_cache --------------------------------------


async def test_clear_all_invalidates_with_no_scope(invalidation_calls):
    await cache_clear_actions_service.clear_all_payment_methods_cache("ampay")

    assert invalidation_calls == [{"brand_id": "ampay"}]
