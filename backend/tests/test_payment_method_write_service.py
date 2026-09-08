"""Unit tests for `app.services.payment_method_write_service` — ported from
`PaymentMethodCompany.save()` / `MerchantPaymentMethod.save()`
(`api_mediator/models.py`).

Redis is mocked out (`safe_invalidate_cache` is patched to record calls
instead of hitting a real client) — what's under test here is the DECISION of
whether/how to invalidate, not the Redis wire protocol itself (that part is
`app.services.cache_invalidation`, exercised separately if/when a live
Redis is available in CI).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("USE_LOCAL_ENV_SECRETS", "true")

from app.models.tenant import (
    Currency,
    DjangoAuthUser,
    Merchant,
    MerchantPaymentMethod,
    PaymentMethod,
    PaymentMethodCompany,
    TenantBase,
)
from app.services import payment_method_write_service


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
    """Replaces the real (Redis-hitting) `safe_invalidate_cache` with a spy."""
    calls: list[dict] = []

    async def _fake(brand_id: str, **kwargs):
        calls.append({"brand_id": brand_id, **kwargs})

    monkeypatch.setattr(payment_method_write_service, "safe_invalidate_cache", _fake)
    return calls


async def _seed_currency(session: AsyncSession, iso_code: str = "USD") -> Currency:
    currency = Currency(iso_code=iso_code, addition_name=iso_code, limit=0)
    session.add(currency)
    await session.flush()
    return currency


async def _seed_payment_method(session: AsyncSession, currency: Currency) -> PaymentMethod:
    pm = PaymentMethod(name="Card", direction="IN", token="tok-1", currency_id=currency.id)
    session.add(pm)
    await session.flush()
    return pm


async def _seed_pmc(session: AsyncSession, payment_method: PaymentMethod, **overrides) -> PaymentMethodCompany:
    from app.models.tenant.mediator import Company

    company = Company(name=f"Partner-{payment_method.id}-{overrides.get('priority', 1)}")
    session.add(company)
    await session.flush()

    defaults = {
        "is_active": True,
        "partner_rate": 5,
        "additional_commission": 0,
        "settlement_commission": 0,
        "daily_amount_limit": None,
        "daily_count_limit": None,
        "transaction_min_limit": None,
        "transaction_max_limit": None,
        "current_daily_amount": 0,
        "current_daily_amount_success": 0,
        "current_daily_count": 0,
        "current_daily_coun_success": 0,
        "last_reset": datetime.now(UTC),
        "changing_rate": False,
        "priority": 1,
        "company_id": company.id,
        "payment_method_id": payment_method.id,
    }
    defaults.update(overrides)
    pmc = PaymentMethodCompany(**defaults)
    session.add(pmc)
    await session.flush()
    return pmc


async def _seed_merchant(session: AsyncSession) -> Merchant:
    user = DjangoAuthUser(
        username=f"demo-{id(session)}",
        email="demo@example.com",
        is_active=True,
        is_staff=False,
        is_superuser=False,
        date_joined=datetime.now(UTC).isoformat(),
    )
    session.add(user)
    await session.flush()
    merchant = Merchant(name="M", user_id=user.id)
    session.add(merchant)
    await session.flush()
    return merchant


async def _seed_mpm(session: AsyncSession, merchant: Merchant, payment_method: PaymentMethod, **overrides):
    defaults = {
        "personal_rate": 10,
        "additional_commission": 0,
        "test_mode": True,
        "block": False,
        "no_callback": False,
        "transaction_min_limit": None,
        "transaction_max_limit": None,
        "only_admin_configure": False,
        "cascade_id": None,
        "merchant_id": merchant.id,
        "payment_method_id": payment_method.id,
    }
    defaults.update(overrides)
    mpm = MerchantPaymentMethod(**defaults)
    session.add(mpm)
    await session.flush()
    return mpm


# --- PaymentMethodCompany -----------------------------------------------


async def test_pmc_changing_partner_rate_alone_does_not_invalidate_cache(session, invalidation_calls):
    currency = await _seed_currency(session)
    pm = await _seed_payment_method(session, currency)
    pmc = await _seed_pmc(session, pm, partner_rate=5)

    before, after = await payment_method_write_service.update_payment_method_company(
        session, brand_id="ampay", pk=pmc.id, values={"partner_rate": 9}
    )

    assert before["partner_rate"] != after["partner_rate"]
    assert invalidation_calls == []  # bug-for-bug: partner_rate isn't a cache-busting field


async def test_pmc_changing_is_active_invalidates_cache_by_currency(session, invalidation_calls):
    currency = await _seed_currency(session, iso_code="EUR")
    pm = await _seed_payment_method(session, currency)
    pmc = await _seed_pmc(session, pm, is_active=True)

    await payment_method_write_service.update_payment_method_company(
        session, brand_id="ampay", pk=pmc.id, values={"is_active": False}
    )

    assert invalidation_calls == [{"brand_id": "ampay", "currency": "EUR"}]


async def test_pmc_no_change_to_tracked_fields_skips_invalidation(session, invalidation_calls):
    currency = await _seed_currency(session)
    pm = await _seed_payment_method(session, currency)
    pmc = await _seed_pmc(session, pm, priority=3)

    await payment_method_write_service.update_payment_method_company(
        session, brand_id="ampay", pk=pmc.id, values={"priority": 3}
    )

    assert invalidation_calls == []


async def test_pmc_rejects_unknown_field(session, invalidation_calls):
    currency = await _seed_currency(session)
    pm = await _seed_payment_method(session, currency)
    pmc = await _seed_pmc(session, pm)

    with pytest.raises(ValueError, match="[Nn]ot editable"):
        await payment_method_write_service.update_payment_method_company(
            session, brand_id="ampay", pk=pmc.id, values={"changing_rate": True}
        )


# --- MerchantPaymentMethod ------------------------------------------------


async def test_mpm_any_save_unconditionally_invalidates_cache_by_merchant(session, invalidation_calls):
    currency = await _seed_currency(session)
    pm = await _seed_payment_method(session, currency)
    merchant = await _seed_merchant(session)
    mpm = await _seed_mpm(session, merchant, pm, no_callback=False)

    # Even a field NOT in the cache-relevant set (no_callback) busts the
    # cache in source - there's no diff check at all on this model.
    await payment_method_write_service.update_merchant_payment_method(
        session, brand_id="ampay", pk=mpm.id, values={"no_callback": True}
    )

    assert invalidation_calls == [{"brand_id": "ampay", "merchant_id": merchant.id}]


async def test_mpm_rejects_unknown_field(session, invalidation_calls):
    currency = await _seed_currency(session)
    pm = await _seed_payment_method(session, currency)
    merchant = await _seed_merchant(session)
    mpm = await _seed_mpm(session, merchant, pm)

    with pytest.raises(ValueError, match="[Nn]ot editable"):
        await payment_method_write_service.update_merchant_payment_method(
            session, brand_id="ampay", pk=mpm.id, values={"merchant_id": 999}
        )


async def test_mpm_rejects_cascade_for_a_different_payment_method(session, invalidation_calls):
    """Port of `MerchantPaymentMethod.clean()`'s cascade/payment_method
    cross-check — a genuine gap in the first pass of this write path,
    closed alongside `app.services.cascade_write_service`."""
    from app.services import cascade_write_service

    currency = await _seed_currency(session)
    pm = await _seed_payment_method(session, currency)
    other_pm = PaymentMethod(name="P2P", direction="IN", token="tok-2", currency_id=currency.id)
    session.add(other_pm)
    await session.flush()
    merchant = await _seed_merchant(session)
    mpm = await _seed_mpm(session, merchant, pm)

    mismatched_cascade = await cascade_write_service.create_cascade(
        session, values={"name": "Other", "payment_method_id": other_pm.id}
    )

    with pytest.raises(ValueError, match="должен соответствовать"):
        await payment_method_write_service.update_merchant_payment_method(
            session, brand_id="ampay", pk=mpm.id, values={"cascade_id": mismatched_cascade["id"]}
        )
    assert invalidation_calls == []  # rejected before any save/invalidate happens


async def test_mpm_accepts_cascade_matching_its_own_payment_method(session, invalidation_calls):
    from app.services import cascade_write_service

    currency = await _seed_currency(session)
    pm = await _seed_payment_method(session, currency)
    merchant = await _seed_merchant(session)
    mpm = await _seed_mpm(session, merchant, pm)

    matching_cascade = await cascade_write_service.create_cascade(
        session, values={"name": "Matching", "payment_method_id": pm.id}
    )

    _, after = await payment_method_write_service.update_merchant_payment_method(
        session, brand_id="ampay", pk=mpm.id, values={"cascade_id": matching_cascade["id"]}
    )
    assert after["cascade_id"] == matching_cascade["id"]
