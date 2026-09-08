"""Unit tests for `app.services.merchant_bulk_actions_service` — ported
from `MerchantAdmin.apply_template_to_merchants` /
`MerchantAdmin.apply_selected_methods_to_merchants` (`personal_account_auth/admin.py`).
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
    PaymentMethodTemplate,
    TemplateMethodMapping,
    TenantBase,
)
from app.services import merchant_bulk_actions_service


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
        username=f"user-{name}",
        email=f"{name}@example.com",
        is_active=True,
        is_staff=False,
        is_superuser=False,
        date_joined=datetime.now(UTC).isoformat(),
    )
    session.add(user)
    await session.flush()
    merchant = Merchant(name=name, user_id=user.id)
    session.add(merchant)
    await session.flush()
    return merchant


async def _seed_currency(session: AsyncSession, iso_code: str = "USD") -> Currency:
    currency = Currency(iso_code=iso_code, addition_name=iso_code, limit=0)
    session.add(currency)
    await session.flush()
    return currency


async def _seed_payment_method(session: AsyncSession, currency: Currency, name: str, direction: str = "IN") -> PaymentMethod:
    pm = PaymentMethod(name=name, direction=direction, token=f"tok-{name}", currency_id=currency.id)
    session.add(pm)
    await session.flush()
    return pm


# --- apply_template_to_merchants -------------------------------------------


async def test_apply_template_creates_methods_for_all_merchants(session):
    currency = await _seed_currency(session)
    pm1 = await _seed_payment_method(session, currency, "Card")
    pm2 = await _seed_payment_method(session, currency, "P2P")
    template = PaymentMethodTemplate(
        name="Standard", description="", currency_id=currency.id, direction="IN", default_personal_rate=Decimal(10)
    )
    session.add(template)
    await session.flush()
    session.add_all(
        [
            TemplateMethodMapping(template_id=template.id, payment_method_id=pm1.id),
            TemplateMethodMapping(template_id=template.id, payment_method_id=pm2.id),
        ]
    )
    await session.flush()
    m1 = await _seed_merchant(session, "m1")
    m2 = await _seed_merchant(session, "m2")

    result = await merchant_bulk_actions_service.apply_template_to_merchants(
        session, merchant_ids=[m1.id, m2.id], template_id=template.id
    )

    assert result.created_count == 4
    assert result.skipped_existing_count == 0


async def test_apply_template_skips_existing_pairs(session):
    currency = await _seed_currency(session)
    pm = await _seed_payment_method(session, currency, "Card")
    template = PaymentMethodTemplate(
        name="Standard", description="", currency_id=currency.id, direction="IN", default_personal_rate=Decimal(10)
    )
    session.add(template)
    await session.flush()
    session.add(TemplateMethodMapping(template_id=template.id, payment_method_id=pm.id))
    await session.flush()
    merchant = await _seed_merchant(session, "m1")
    session.add(
        MerchantPaymentMethod(merchant_id=merchant.id, payment_method_id=pm.id, personal_rate=Decimal(5))
    )
    await session.flush()

    result = await merchant_bulk_actions_service.apply_template_to_merchants(
        session, merchant_ids=[merchant.id], template_id=template.id
    )

    assert result.created_count == 0
    assert result.skipped_existing_count == 1


async def test_apply_template_rejects_unknown_template(session):
    merchant = await _seed_merchant(session, "m1")
    with pytest.raises(RecordNotFoundError):
        await merchant_bulk_actions_service.apply_template_to_merchants(
            session, merchant_ids=[merchant.id], template_id=999
        )


async def test_apply_template_rejects_empty_merchant_list(session):
    with pytest.raises(ValueError, match="[Нн]е выбрано"):
        await merchant_bulk_actions_service.apply_template_to_merchants(session, merchant_ids=[], template_id=1)


# --- apply_selected_methods_to_merchants ------------------------------------


async def test_apply_selected_methods_creates_only_matching_currency_and_direction(session):
    currency = await _seed_currency(session, "USD")
    other_currency = await _seed_currency(session, "EUR")
    pm_match = await _seed_payment_method(session, currency, "Card", direction="IN")
    pm_wrong_currency = await _seed_payment_method(session, other_currency, "Card2", direction="IN")
    pm_wrong_direction = await _seed_payment_method(session, currency, "Card3", direction="OUT")
    merchant = await _seed_merchant(session, "m1")

    result = await merchant_bulk_actions_service.apply_selected_methods_to_merchants(
        session,
        merchant_ids=[merchant.id],
        currency_id=currency.id,
        direction="IN",
        method_ids=[pm_match.id, pm_wrong_currency.id, pm_wrong_direction.id],
        personal_rate=Decimal(15),
        transaction_min_limit=None,
        transaction_max_limit=None,
        test_mode=True,
        only_admin_configure=False,
    )

    assert result.created_count == 1  # only pm_match survives the currency+direction filter


async def test_apply_selected_methods_skips_existing_pairs(session):
    currency = await _seed_currency(session)
    pm = await _seed_payment_method(session, currency, "Card")
    merchant = await _seed_merchant(session, "m1")
    session.add(MerchantPaymentMethod(merchant_id=merchant.id, payment_method_id=pm.id, personal_rate=Decimal(1)))
    await session.flush()

    result = await merchant_bulk_actions_service.apply_selected_methods_to_merchants(
        session,
        merchant_ids=[merchant.id],
        currency_id=currency.id,
        direction="IN",
        method_ids=[pm.id],
        personal_rate=Decimal(15),
        transaction_min_limit=None,
        transaction_max_limit=None,
        test_mode=True,
        only_admin_configure=False,
    )

    assert result.created_count == 0
    assert result.skipped_existing_count == 1


async def test_apply_selected_methods_rejects_empty_method_list(session):
    merchant = await _seed_merchant(session, "m1")
    with pytest.raises(ValueError, match="платёжного метода"):
        await merchant_bulk_actions_service.apply_selected_methods_to_merchants(
            session,
            merchant_ids=[merchant.id],
            currency_id=1,
            direction="IN",
            method_ids=[],
            personal_rate=Decimal(1),
            transaction_min_limit=None,
            transaction_max_limit=None,
            test_mode=True,
            only_admin_configure=False,
        )


# --- validate_merchant_ids ---------------------------------------------------


async def test_validate_merchant_ids_rejects_missing_merchant(session):
    merchant = await _seed_merchant(session, "m1")
    with pytest.raises(RecordNotFoundError):
        await merchant_bulk_actions_service.validate_merchant_ids(session, [merchant.id, 999])


async def test_validate_merchant_ids_accepts_known_merchants(session):
    merchant = await _seed_merchant(session, "m1")
    await merchant_bulk_actions_service.validate_merchant_ids(session, [merchant.id])
