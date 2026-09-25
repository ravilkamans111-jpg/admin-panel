"""Unit tests for `app.services.generic_write_service` — create/update/delete
for "plain" admin models (no dedicated write service), config-driven off
the admin registry."""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("USE_LOCAL_ENV_SECRETS", "true")

from app.core.exceptions import RecordNotFoundError
from app.models.tenant import Bank, TenantBase
from app.registry.admin_models import get_config
from app.services import generic_write_service


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        yield s
    await engine.dispose()


BANK_CONFIG = get_config("banks")


async def test_create_record_builds_and_flushes_instance(session: AsyncSession):
    after = await generic_write_service.create_record(session, config=BANK_CONFIG, values={"name": "Tinkoff"})
    assert after["name"] == "Tinkoff"
    assert after["id"] is not None


async def test_create_record_rejects_unknown_field(session: AsyncSession):
    with pytest.raises(ValueError, match="Not creatable"):
        await generic_write_service.create_record(session, config=BANK_CONFIG, values={"nope": "x"})


async def test_update_record_applies_casted_values(session: AsyncSession):
    bank = Bank(name="Old Name")
    session.add(bank)
    await session.flush()

    before, after = await generic_write_service.update_record(
        session, config=BANK_CONFIG, pk=bank.id, values={"name": "New Name"}
    )
    assert before["name"] == "Old Name"
    assert after["name"] == "New Name"


async def test_update_record_rejects_unknown_pk(session: AsyncSession):
    with pytest.raises(RecordNotFoundError):
        await generic_write_service.update_record(session, config=BANK_CONFIG, pk=999, values={"name": "X"})


async def test_delete_record_removes_instance(session: AsyncSession):
    bank = Bank(name="To Delete")
    session.add(bank)
    await session.flush()
    bank_id = bank.id

    before = await generic_write_service.delete_record(session, config=BANK_CONFIG, pk=bank_id)
    assert before["name"] == "To Delete"

    with pytest.raises(RecordNotFoundError):
        await generic_write_service.update_record(session, config=BANK_CONFIG, pk=bank_id, values={"name": "X"})


async def test_create_record_rejects_when_not_creatable(session: AsyncSession):
    non_creatable_config = get_config("transactions")
    with pytest.raises(ValueError, match="not creatable"):
        await generic_write_service.create_record(session, config=non_creatable_config, values={})
