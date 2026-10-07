"""Port of `TransactionSaveService.pre_save_from_admin_transaction`'s second
block + `ConversionStatisticService` (source: conversion_statistic app).

After the balance mutations succeed, source updates three per-day roll-ups —
per payment method, per merchant payment method, per partner (payment method
company). The whole block is best-effort: any failure is logged and the
transaction edit still stands ("Транзакция обновлена, но стата не записана"),
so here it runs inside a SAVEPOINT that is rolled back as a unit on error —
the same all-or-nothing the source gets from its `atomic()` wrapper.

Source behaviours preserved on purpose (they decide what the numbers are):
  - `old_snapshot is None` (brand-new transaction, e.g. created by a
    Settlement) → source dereferences `old_transaction.status` and crashes
    into its `except`, so no stats are written. Same here.
  - The `not result_old` branch (count a new request) is unreachable from the
    admin path: `result_old` is always one of ACCEPTED/SUCCESS/ERROR.
  - A missing row is created dated TODAY (auto_now_add) and then re-read by
    the transaction's own date; for an old transaction with no row that read
    fails and the whole block is skipped. Same here.
  - `conversion_percent_paid_orders` divides by `amount_requests_success`
    whenever `amount_paid_orders != 0`; a zero denominator raises and the
    block is skipped, exactly as source's `Decimal` division does.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_brand_timezone
from app.models.tenant import (
    ConversionStatisticsMerchantNew,
    ConversionStatisticsNew,
    ConversionStatisticsPartnersNew,
    MerchantPaymentMethod,
    PaymentMethodCompany,
    Transaction,
)
from app.services.balance_math import TransactionSnapshot

logger = logging.getLogger(__name__)

ACCEPTED, SUCCESS, ERROR = "ACCEPTED", "SUCCESS", "ERROR"
_TWO_PLACES = Decimal("0.01")
_FOUR_PLACES = Decimal("0.0001")


def _stat_result(status: str) -> str:
    return ACCEPTED if status == "ACCEPTED" else SUCCESS if status == "SUCCESS" else ERROR


def _local_date(moment: datetime | None, tz: ZoneInfo) -> date:
    if moment is None:
        return datetime.now(tz).date()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(tz).date()


def _apply_counters(
    stat: Any, *, txn: Transaction, old: TransactionSnapshot, result: str, result_old: str
) -> None:
    if result_old == ACCEPTED and result == SUCCESS:
        stat.num_paid_orders += 1
        stat.amount_paid_orders += txn.amount
    elif result_old == SUCCESS and result == ERROR:
        stat.num_paid_orders -= 1
        stat.amount_paid_orders -= old.amount
    elif result_old == SUCCESS and result == SUCCESS and txn.amount != old.amount:
        stat.amount_paid_orders -= old.amount
        stat.amount_paid_orders += txn.amount
    elif result_old == ERROR and result == SUCCESS:
        stat.num_paid_orders += 1
        stat.amount_paid_orders += txn.amount


def _recompute(stat: Any, tz: ZoneInfo) -> None:
    """Mirror of the stat models' `save()` override."""
    stat.num_requests = stat.num_requests or 0
    stat.num_requests_success = stat.num_requests_success or 0
    stat.num_paid_orders = stat.num_paid_orders or 0
    stat.amount_requests = stat.amount_requests or Decimal(0)
    stat.amount_requests_success = stat.amount_requests_success or Decimal(0)
    stat.amount_paid_orders = stat.amount_paid_orders or Decimal(0)
    stat.date_only = _local_date(stat.date_create, tz)
    stat.conversion_percent = (
        Decimal(stat.num_requests_success / stat.num_requests * 100).quantize(_TWO_PLACES, rounding=ROUND_HALF_EVEN)
        if stat.num_requests != 0
        else Decimal(0)
    )
    # Raises decimal.DivisionByZero when amount_requests_success == 0 — intended (see module docstring).
    stat.conversion_percent_paid_orders = (
        (stat.amount_paid_orders / stat.amount_requests_success * 100).quantize(_TWO_PLACES, rounding=ROUND_HALF_EVEN)
        if stat.amount_paid_orders != 0
        else Decimal(0)
    )
    stat.amount_paid_orders = stat.amount_paid_orders.quantize(_FOUR_PLACES, rounding=ROUND_HALF_EVEN)
    stat.data_update = datetime.now(UTC)


async def _get_or_create(
    session: AsyncSession, model: type, fk_column: str, fk_value: int, txn_date: date, tz: ZoneInfo
) -> Any:
    query = (
        select(model)
        .where(getattr(model, fk_column) == fk_value, model.date_only == txn_date)
        .with_for_update()
    )
    stat = (await session.execute(query)).scalar_one_or_none()
    if stat is not None:
        return stat
    now = datetime.now(UTC)
    fresh = model(
        **{fk_column: fk_value},
        num_requests=0, num_requests_success=0, num_paid_orders=0,
        amount_requests=Decimal(0), amount_requests_success=Decimal(0), amount_paid_orders=Decimal(0),
        conversion_percent=Decimal(0), conversion_percent_paid_orders=Decimal(0),
        date_create=now, data_update=now, date_only=_local_date(now, tz),
    )
    session.add(fresh)
    await session.flush()
    # Source re-reads by the transaction's date; for a non-today date this raises.
    return (await session.execute(query)).scalar_one()


async def update_for_admin_edit(
    session: AsyncSession, *, brand_id: str, txn: Transaction, old_snapshot: TransactionSnapshot | None
) -> None:
    if old_snapshot is None:
        logger.info("Conversion stats skipped for transaction %s: no previous state (as in source)", txn.id)
        return
    try:
        async with session.begin_nested():
            tz = ZoneInfo(get_brand_timezone(brand_id))
            pmc = await session.get(PaymentMethodCompany, txn.payment_method_company_id)
            mpm = (
                await session.execute(
                    select(MerchantPaymentMethod).where(
                        MerchantPaymentMethod.merchant_id == txn.merchant_id,
                        MerchantPaymentMethod.payment_method_id == pmc.payment_method_id,
                    )
                )
            ).scalar_one()
            txn_date = _local_date(txn.date_create, tz)
            result, result_old = _stat_result(txn.status), _stat_result(old_snapshot.status)

            targets = (
                (ConversionStatisticsNew, "payment_method_id", pmc.payment_method_id),
                (ConversionStatisticsMerchantNew, "merchant_payment_method_id", mpm.id),
                (ConversionStatisticsPartnersNew, "payment_method_company_id", pmc.id),
            )
            for model, fk_column, fk_value in targets:
                stat = await _get_or_create(session, model, fk_column, fk_value, txn_date, tz)
                _apply_counters(stat, txn=txn, old=old_snapshot, result=result, result_old=result_old)
                _recompute(stat, tz)
            await session.flush()
    except Exception:
        logger.exception("Ошибка обновления статистики. Транзакция обновлена, но стата не записана.")
