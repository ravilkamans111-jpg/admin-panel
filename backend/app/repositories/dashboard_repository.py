"""Cross-model aggregate queries for the dashboard.

Kept separate from `admin_repository` since these aggregate across models
rather than listing rows of one — a different query shape than the generic
admin engine handles. Pure data access; the service layer
(`app.services.dashboard_service`) shapes this into the response dict.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Currency, Merchant, MerchantBalance, Transaction


async def count_merchants(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(Merchant))
    return result.scalar_one()


async def count_transactions(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(Transaction))
    return result.scalar_one()


async def transactions_by_status(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(select(Transaction.status, func.count()).group_by(Transaction.status))
    return {status: count for status, count in result.all()}


async def balances_by_currency(session: AsyncSession) -> list[dict[str, str | int]]:
    result = await session.execute(
        select(
            MerchantBalance.currency_id,
            Currency.iso_code,
            func.sum(MerchantBalance.balance),
            func.sum(MerchantBalance.blocked_balance_in),
            func.sum(MerchantBalance.blocked_balance_out),
        )
        .join(Currency, Currency.id == MerchantBalance.currency_id)
        .group_by(MerchantBalance.currency_id, Currency.iso_code)
    )
    return [
        {
            "currency_id": currency_id,
            "currency_code": iso_code,
            "total_balance": str(total_balance or 0),
            "total_blocked_in": str(total_blocked_in or 0),
            "total_blocked_out": str(total_blocked_out or 0),
        }
        for currency_id, iso_code, total_balance, total_blocked_in, total_blocked_out in result.all()
    ]
