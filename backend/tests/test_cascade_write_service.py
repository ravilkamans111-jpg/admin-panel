"""Unit tests for `app.services.cascade_write_service` — the validation
state machine ported from `PaymentMethodCascadeAdmin` /
`PaymentMethodCascadeItemInline` / `PaymentMethodCascadeItem.clean()`.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("USE_LOCAL_ENV_SECRETS", "true")

from app.core.exceptions import RecordNotFoundError
from app.models.tenant import Currency, PaymentMethod, PaymentMethodCompany, TenantBase
from app.models.tenant.mediator import Company
from app.services import cascade_write_service


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        yield s
    await engine.dispose()


async def _seed_payment_method(session: AsyncSession, name: str = "Card") -> PaymentMethod:
    iso_code = f"U{abs(hash(name)) % 100:02d}"
    currency = Currency(iso_code=iso_code, addition_name=iso_code, limit=0)
    session.add(currency)
    await session.flush()
    pm = PaymentMethod(name=name, direction="IN", token=f"tok-{name}", currency_id=currency.id)
    session.add(pm)
    await session.flush()
    return pm


async def _seed_pmc(session: AsyncSession, payment_method: PaymentMethod, *, priority: int = 1) -> PaymentMethodCompany:
    from datetime import UTC, datetime

    company = Company(name=f"Partner-{payment_method.id}-{priority}")
    session.add(company)
    await session.flush()
    pmc = PaymentMethodCompany(
        is_active=True,
        partner_rate=5,
        additional_commission=0,
        settlement_commission=0,
        daily_amount_limit=None,
        daily_count_limit=None,
        transaction_min_limit=None,
        transaction_max_limit=None,
        current_daily_amount=0,
        current_daily_amount_success=0,
        current_daily_count=0,
        current_daily_coun_success=0,
        last_reset=datetime.now(UTC),
        changing_rate=False,
        priority=priority,
        company_id=company.id,
        payment_method_id=payment_method.id,
    )
    session.add(pmc)
    await session.flush()
    return pmc


# --- Cascade ---------------------------------------------------------------


async def test_create_cascade_rejects_duplicate_name_for_same_payment_method(session):
    pm = await _seed_payment_method(session)
    await cascade_write_service.create_cascade(
        session, values={"name": "Основной", "payment_method_id": pm.id}
    )

    with pytest.raises(ValueError, match="уже существует"):
        await cascade_write_service.create_cascade(
            session, values={"name": "Основной", "payment_method_id": pm.id}
        )


async def test_create_cascade_allows_same_name_for_different_payment_method(session):
    pm1 = await _seed_payment_method(session, name="Card")
    pm2 = await _seed_payment_method(session, name="P2P")

    a = await cascade_write_service.create_cascade(session, values={"name": "Основной", "payment_method_id": pm1.id})
    b = await cascade_write_service.create_cascade(session, values={"name": "Основной", "payment_method_id": pm2.id})

    assert a["id"] != b["id"]


async def test_update_cascade_rejects_payment_method_field(session):
    pm = await _seed_payment_method(session)
    cascade = await cascade_write_service.create_cascade(session, values={"name": "A", "payment_method_id": pm.id})

    with pytest.raises(ValueError, match="[Nn]ot editable"):
        await cascade_write_service.update_cascade(session, pk=cascade["id"], values={"payment_method_id": 999})


async def test_update_cascade_rejects_renaming_to_taken_name(session):
    pm = await _seed_payment_method(session)
    await cascade_write_service.create_cascade(session, values={"name": "A", "payment_method_id": pm.id})
    b = await cascade_write_service.create_cascade(session, values={"name": "B", "payment_method_id": pm.id})

    with pytest.raises(ValueError, match="уже существует"):
        await cascade_write_service.update_cascade(session, pk=b["id"], values={"name": "A"})


async def test_update_cascade_allows_renaming_to_its_own_current_name(session):
    pm = await _seed_payment_method(session)
    cascade = await cascade_write_service.create_cascade(session, values={"name": "A", "payment_method_id": pm.id})

    _, after = await cascade_write_service.update_cascade(
        session, pk=cascade["id"], values={"name": "A", "is_active": False}
    )
    assert after["is_active"] is False


# --- Cascade items -----------------------------------------------------------


async def test_create_item_rejects_mismatched_payment_method(session):
    pm_cascade = await _seed_payment_method(session, name="Card")
    pm_other = await _seed_payment_method(session, name="P2P")
    cascade = await cascade_write_service.create_cascade(
        session, values={"name": "A", "payment_method_id": pm_cascade.id}
    )
    wrong_pmc = await _seed_pmc(session, pm_other)

    with pytest.raises(ValueError, match="должен соответствовать"):
        await cascade_write_service.create_cascade_item(
            session,
            cascade_id=cascade["id"],
            values={"payment_method_company_id": wrong_pmc.id, "priority": 1},
        )


async def test_create_item_rejects_duplicate_priority_within_cascade(session):
    pm = await _seed_payment_method(session)
    cascade = await cascade_write_service.create_cascade(session, values={"name": "A", "payment_method_id": pm.id})
    pmc1 = await _seed_pmc(session, pm, priority=1)
    pmc2 = await _seed_pmc(session, pm, priority=2)

    await cascade_write_service.create_cascade_item(
        session, cascade_id=cascade["id"], values={"payment_method_company_id": pmc1.id, "priority": 1}
    )
    with pytest.raises(ValueError, match="[Пп]риоритет"):
        await cascade_write_service.create_cascade_item(
            session, cascade_id=cascade["id"], values={"payment_method_company_id": pmc2.id, "priority": 1}
        )


async def test_create_item_allows_same_priority_in_a_different_cascade(session):
    pm = await _seed_payment_method(session)
    cascade_a = await cascade_write_service.create_cascade(session, values={"name": "A", "payment_method_id": pm.id})
    cascade_b = await cascade_write_service.create_cascade(session, values={"name": "B", "payment_method_id": pm.id})
    pmc1 = await _seed_pmc(session, pm, priority=1)
    pmc2 = await _seed_pmc(session, pm, priority=2)

    item_a = await cascade_write_service.create_cascade_item(
        session, cascade_id=cascade_a["id"], values={"payment_method_company_id": pmc1.id, "priority": 1}
    )
    item_b = await cascade_write_service.create_cascade_item(
        session, cascade_id=cascade_b["id"], values={"payment_method_company_id": pmc2.id, "priority": 1}
    )
    assert item_a["priority"] == item_b["priority"] == 1


async def test_update_item_allows_keeping_its_own_priority(session):
    pm = await _seed_payment_method(session)
    cascade = await cascade_write_service.create_cascade(session, values={"name": "A", "payment_method_id": pm.id})
    pmc = await _seed_pmc(session, pm, priority=1)
    item = await cascade_write_service.create_cascade_item(
        session, cascade_id=cascade["id"], values={"payment_method_company_id": pmc.id, "priority": 1}
    )

    _, after = await cascade_write_service.update_cascade_item(
        session, pk=item["id"], values={"priority": 1, "is_active": False}
    )
    assert after["priority"] == 1
    assert after["is_active"] is False


async def test_update_item_rejects_colliding_with_another_items_priority(session):
    pm = await _seed_payment_method(session)
    cascade = await cascade_write_service.create_cascade(session, values={"name": "A", "payment_method_id": pm.id})
    pmc1 = await _seed_pmc(session, pm, priority=1)
    pmc2 = await _seed_pmc(session, pm, priority=2)
    await cascade_write_service.create_cascade_item(
        session, cascade_id=cascade["id"], values={"payment_method_company_id": pmc1.id, "priority": 1}
    )
    item2 = await cascade_write_service.create_cascade_item(
        session, cascade_id=cascade["id"], values={"payment_method_company_id": pmc2.id, "priority": 2}
    )

    with pytest.raises(ValueError, match="[Пп]риоритет"):
        await cascade_write_service.update_cascade_item(session, pk=item2["id"], values={"priority": 1})


async def test_delete_item_removes_it(session):
    pm = await _seed_payment_method(session)
    cascade = await cascade_write_service.create_cascade(session, values={"name": "A", "payment_method_id": pm.id})
    pmc = await _seed_pmc(session, pm, priority=1)
    item = await cascade_write_service.create_cascade_item(
        session, cascade_id=cascade["id"], values={"payment_method_company_id": pmc.id, "priority": 1}
    )

    await cascade_write_service.delete_cascade_item(session, pk=item["id"])

    with pytest.raises(RecordNotFoundError):
        await cascade_write_service.update_cascade_item(session, pk=item["id"], values={"priority": 5})


# --- MerchantPaymentMethod cascade cross-check ------------------------------


async def test_validate_merchant_payment_method_cascade_rejects_mismatch(session):
    pm_cascade = await _seed_payment_method(session, name="Card")
    pm_merchant = await _seed_payment_method(session, name="P2P")
    cascade = await cascade_write_service.create_cascade(
        session, values={"name": "A", "payment_method_id": pm_cascade.id}
    )

    with pytest.raises(ValueError, match="должен соответствовать"):
        await cascade_write_service.validate_merchant_payment_method_cascade(
            session, payment_method_id=pm_merchant.id, cascade_id=cascade["id"]
        )


async def test_validate_merchant_payment_method_cascade_accepts_match(session):
    pm = await _seed_payment_method(session)
    cascade = await cascade_write_service.create_cascade(session, values={"name": "A", "payment_method_id": pm.id})

    # Should not raise.
    await cascade_write_service.validate_merchant_payment_method_cascade(
        session, payment_method_id=pm.id, cascade_id=cascade["id"]
    )


async def test_validate_merchant_payment_method_cascade_noop_when_no_cascade(session):
    # Should not raise even with a bogus payment_method_id — cascade_id=None short-circuits.
    await cascade_write_service.validate_merchant_payment_method_cascade(
        session, payment_method_id=999, cascade_id=None
    )
