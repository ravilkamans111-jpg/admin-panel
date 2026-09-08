"""Unit tests for `app.services.antifraud_write_service` — the diff logic
ported from `AntiFraudBlockedMerchantUsers.save(from_admin=True, old_value=...)`.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("USE_LOCAL_ENV_SECRETS", "true")

from app.models.tenant import AntiFraudBlockedMerchantUsers, DjangoAuthUser, Merchant, TenantBase
from app.services import antifraud_write_service


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        yield s
    await engine.dispose()


async def _seed_block(session: AsyncSession, **overrides) -> AntiFraudBlockedMerchantUsers:
    user = DjangoAuthUser(
        username="demo", email="demo@example.com", is_active=True, is_staff=False,
        is_superuser=False, date_joined=datetime.now(UTC).isoformat(),
    )
    session.add(user)
    await session.flush()
    merchant = Merchant(name="M", user_id=user.id)
    session.add(merchant)
    await session.flush()

    defaults = {
        "merchant_name": "demo: M",
        "user_id": "external-user-1",
        "second_chance": False,
        "second_chance_counter": 0,
        "second_chance_date": None,
        "ban_date": datetime.now(UTC),
        "date_create": datetime.now(UTC),
        "date_update": datetime.now(UTC),
        "permanent_ban": False,
        "merchant_id": merchant.id,
    }
    defaults.update(overrides)
    block = AntiFraudBlockedMerchantUsers(**defaults)
    session.add(block)
    await session.flush()
    return block


async def test_granting_second_chance_increments_counter_and_sets_date(session: AsyncSession):
    block = await _seed_block(session, second_chance=False, second_chance_counter=2)

    before, after = await antifraud_write_service.update_antifraud_block(
        session, pk=block.id, values={"second_chance": True}
    )

    assert before["second_chance"] is False
    assert after["second_chance"] is True
    assert after["second_chance_counter"] == 3
    assert after["second_chance_date"] is not None


async def test_revoking_second_chance_sets_ban_date(session: AsyncSession):
    block = await _seed_block(session, second_chance=True, ban_date=None)

    _, after = await antifraud_write_service.update_antifraud_block(
        session, pk=block.id, values={"second_chance": False}
    )

    assert after["second_chance"] is False
    assert after["ban_date"] is not None


async def test_setting_permanent_ban_sets_ban_date(session: AsyncSession):
    block = await _seed_block(session, permanent_ban=False, ban_date=None)

    _, after = await antifraud_write_service.update_antifraud_block(
        session, pk=block.id, values={"permanent_ban": True}
    )

    assert after["permanent_ban"] is True
    assert after["ban_date"] is not None


async def test_no_change_to_second_chance_or_permanent_ban_leaves_dates_untouched(session: AsyncSession):
    block = await _seed_block(session, second_chance=True, permanent_ban=False, second_chance_date=None)

    _, after = await antifraud_write_service.update_antifraud_block(
        session, pk=block.id, values={"user_id": "renamed-user"}
    )

    # second_chance stayed True (unchanged) -> none of the three diff
    # branches fire; second_chance_date stays None, counter unchanged.
    assert after["user_id"] == "renamed-user"
    assert after["second_chance_date"] is None
    assert after["second_chance_counter"] == 0


async def test_rejects_unknown_field(session: AsyncSession):
    block = await _seed_block(session)
    with pytest.raises(ValueError, match="[Nn]ot editable"):
        await antifraud_write_service.update_antifraud_block(
            session, pk=block.id, values={"second_chance_counter": 99}
        )
