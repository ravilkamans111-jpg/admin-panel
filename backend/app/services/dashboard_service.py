"""Сборка сводки для главного дашборда из нескольких моделей сразу."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import dashboard_repository as repo


async def get_summary(session: AsyncSession, *, brand_id: str) -> dict[str, Any]:
    return {
        "brand_id": brand_id,
        "total_merchants": await repo.count_merchants(session),
        "total_transactions": await repo.count_transactions(session),
        "transactions_by_status": await repo.transactions_by_status(session),
        "balances_by_currency": await repo.balances_by_currency(session),
    }
