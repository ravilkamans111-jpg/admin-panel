"""Creates schema + sample data in a local tenant Postgres (for dev/testing only).

Usage:
    cd backend
    uv run python ../scripts/seed_tenant_db.py ampay
    uv run python ../scripts/seed_tenant_db.py rajapay
    uv run python ../scripts/seed_tenant_db.py quiet-forest

Never point this at a real brand database — it calls `create_all()`, which
is only safe against an empty throwaway DB. Real brand DBs are owned and
migrated by the Django monoliths; this service only ever reads from them.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.config import get_brand_db_config  # noqa: E402
from app.models.tenant import (  # noqa: E402
    Bank,
    Company,
    CompanyBalance,
    Currency,
    DjangoAuthUser,
    Merchant,
    MerchantBalance,
    PaymentMethod,
    PaymentMethodCompany,
    TenantBase,
    Transaction,
)

BRAND_ALIASES = {"quiet-forest": "quiet-forest", "quiet_forest": "quiet-forest"}


async def seed(brand_id: str) -> None:
    brand_id = BRAND_ALIASES.get(brand_id, brand_id)
    db_config = get_brand_db_config(brand_id)
    engine = create_async_engine(db_config.async_dsn)

    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = DjangoAuthUser(
            username=f"{brand_id}_demo_user",
            email=f"demo@{brand_id}.example",
            is_active=True,
            is_staff=False,
            is_superuser=False,
            date_joined=datetime.now(timezone.utc).isoformat(),
        )
        session.add(user)
        await session.flush()

        merchant = Merchant(
            name="Demo Merchant",
            public_key="demo_public_key_" + brand_id,
            private_key="demo_private_key_" + brand_id,
            user_id=user.id,
        )
        session.add(merchant)
        await session.flush()

        currency = Currency(iso_code="USD", addition_name="US Dollar", limit=0)
        bank = Bank(name="Demo Bank")
        session.add_all([currency, bank])
        await session.flush()

        merchant_balance = MerchantBalance(
            merchant_id=merchant.id,
            currency_id=currency.id,
            balance=Decimal("1000.00"),
            blocked_balance_in=Decimal("50.00"),
            blocked_balance_out=Decimal("0.00"),
        )
        session.add(merchant_balance)

        company = Company(name="Demo Partner")
        session.add(company)
        await session.flush()

        company_balance = CompanyBalance(
            company_id=company.id,
            currency_id=currency.id,
            available_balance=Decimal("5000.00"),
            blocked_balance_in=Decimal("0.00"),
            blocked_balance_out=Decimal("0.00"),
            our_income=Decimal("120.00"),
            clients_funds=Decimal("4800.00"),
        )
        session.add(company_balance)

        payment_method = PaymentMethod(
            name="Demo Method", direction="IN", token=f"tok_{brand_id}_demo", currency_id=currency.id
        )
        session.add(payment_method)
        await session.flush()

        pmc = PaymentMethodCompany(
            is_active=True,
            partner_rate=Decimal("2.50"),
            company_id=company.id,
            payment_method_id=payment_method.id,
            last_reset=datetime.now(timezone.utc),
        )
        session.add(pmc)
        await session.flush()

        transaction = Transaction(
            tracker_id=f"trk_{brand_id}_001",
            partner_system_id="partner_001",
            merchant_system_id="merchant_001",
            status="SUCCESS",
            direction="IN",
            date_create=datetime.now(timezone.utc),
            date_update=datetime.now(timezone.utc),
            amount=Decimal("100.00"),
            commission=Decimal("5.00"),
            partner_income=Decimal("2.50"),
            pure_our_income=Decimal("2.50"),
            amount_after_commission=Decimal("95.00"),
            merchant_id=merchant.id,
            payment_method_company_id=pmc.id,
        )
        session.add(transaction)

        # "AmPay" company + a "settlement" payment method/company config —
        # required by app.services.settlement_write_service for TO_MERCHANT/
        # FROM_MERCHANT settlements (it looks up a PaymentMethodCompany named
        # "settlement" for the resolved company+currency+direction). Without
        # this, creating a settlement 404s/400s with a clear error instead of
        # silently doing the wrong thing — but it's needed for local testing.
        ampay_company = Company(name="AmPay")
        session.add(ampay_company)
        await session.flush()

        settlement_method_out = PaymentMethod(
            name="settlement", direction="OUT", token=f"tok_{brand_id}_settle_out", currency_id=currency.id
        )
        session.add(settlement_method_out)
        await session.flush()

        ampay_settlement_pmc = PaymentMethodCompany(
            is_active=True,
            partner_rate=Decimal("0"),
            company_id=ampay_company.id,
            payment_method_id=settlement_method_out.id,
            last_reset=datetime.now(timezone.utc),
        )
        session.add(ampay_settlement_pmc)

        await session.commit()

    await engine.dispose()
    print(f"Seeded {brand_id} tenant DB.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: seed_tenant_db.py <brand_id>")
        sys.exit(1)
    asyncio.run(seed(sys.argv[1]))
