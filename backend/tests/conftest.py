from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

os.environ.setdefault("USE_LOCAL_ENV_SECRETS", "true")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")
os.environ.setdefault("CONTROL_PLANE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import CurrentUser, get_current_user, get_tenant_session
from app.core.security import hash_password
from app.db.control_plane import get_control_plane_session
from app.main import app
from app.models.control_plane import AdminUser, BrandAccess, BrandRole, ControlPlaneBase
from app.models.tenant import (
    Company,
    CompanyBalance,
    Currency,
    DjangoAuthUser,
    Merchant,
    PaymentMethod,
    PaymentMethodCompany,
    TenantBase,
    Transaction,
)


@pytest_asyncio.fixture
async def control_plane_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(ControlPlaneBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_control_plane_session] = override

    async with session_factory() as session:
        yield session

    app.dependency_overrides.pop(get_control_plane_session, None)
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_admin_user(control_plane_session: AsyncSession) -> AdminUser:
    user = AdminUser(
        email="viewer@example.com",
        password_hash=hash_password("correct-horse-battery-staple"),
        full_name="Test Viewer",
        is_active=True,
        is_superuser=False,
    )
    control_plane_session.add(user)
    await control_plane_session.flush()
    control_plane_session.add(BrandAccess(admin_user_id=user.id, brand_id="ampay", role=BrandRole.VIEWER))
    await control_plane_session.commit()
    await control_plane_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def tenant_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = DjangoAuthUser(
            username="demo", email="demo@ampay.example", is_active=True, is_staff=False,
            is_superuser=False, date_joined=datetime.now(UTC).isoformat(),
        )
        session.add(user)
        await session.flush()

        merchant = Merchant(name="Acme Merchant", public_key="pub123", private_key="priv123", user_id=user.id)
        session.add(merchant)
        await session.flush()

        currency = Currency(iso_code="USD", addition_name="US Dollar", limit=0)
        session.add(currency)
        await session.flush()

        company = Company(name="Acme Partner")
        session.add(company)
        await session.flush()

        company_balance = CompanyBalance(
            company_id=company.id, currency_id=currency.id,
            available_balance=Decimal("1000.00"), blocked_balance_in=Decimal(0),
            blocked_balance_out=Decimal(0), our_income=Decimal(10), clients_funds=Decimal(990),
        )
        session.add(company_balance)

        payment_method = PaymentMethod(name="Card", direction="IN", token="tok1", currency_id=currency.id)
        session.add(payment_method)
        await session.flush()

        pmc = PaymentMethodCompany(
            is_active=True, partner_rate=Decimal("2.0"), company_id=company.id,
            payment_method_id=payment_method.id, last_reset=datetime.now(UTC),
        )
        session.add(pmc)
        await session.flush()

        for i in range(3):
            session.add(
                Transaction(
                    tracker_id=f"trk_{i}",
                    partner_system_id="p1",
                    merchant_system_id=f"m{i}",
                    status="SUCCESS" if i % 2 == 0 else "DECLINED",
                    direction="IN",
                    date_create=datetime.now(UTC),
                    date_update=datetime.now(UTC),
                    amount=Decimal("100.00"),
                    commission=Decimal("5.00"),
                    partner_income=Decimal("2.50"),
                    pure_our_income=Decimal("2.50"),
                    amount_after_commission=Decimal("95.00"),
                    merchant_id=merchant.id,
                    payment_method_company_id=pmc.id,
                )
            )
        await session.commit()

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def authed_client(tenant_engine) -> AsyncClient:
    session_factory = async_sessionmaker(tenant_engine, expire_on_commit=False)

    async def override_tenant_session():
        async with session_factory() as session:
            yield session

    async def override_current_user():
        return CurrentUser(admin_user_id=1, brand_id="ampay", role="viewer")

    app.dependency_overrides[get_tenant_session] = override_tenant_session
    app.dependency_overrides[get_current_user] = override_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.pop(get_tenant_session, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest_asyncio.fixture
async def raw_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
