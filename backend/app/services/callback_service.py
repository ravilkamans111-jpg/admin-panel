"""Port of the two bulk admin actions in source's
`personal_account_transaction/admin.py`:
  - `TransactionAdmin.send_callbacks_to_merchants` ("Отправить коллбэки
    выбранным мерчантам") — enqueues the REAL `CallbacksService
    .send_message_to_merchant` Celery task by name onto the brand's own
    Celery broker (see `app.db.tenant_celery`), so the monolith's own
    workers do the actual signing/HTTP send — this service never
    reimplements that signature logic, exactly like it never reimplements
    Redis cache invalidation (`app.services.cache_invalidation`).
  - `SettlementsAdmin.send_callbacks_to_tg_user` ("Отправить коллбэки
    выбранным пользователям в телеграмм") — NOT a Celery task in source
    (`CallbacksService.send_message_to_tg_user` is called directly, inline,
    from the admin action), so there is no existing worker to hand this off
    to. Ported here as a direct HTTP POST to `BOT_CALLBACK_URL`, mirroring
    `CallbacksService.create_callback_settlement_data` field-for-field.

Deliberately NOT ported: the Mostbet-specific signing branch in source's
`_get_callback_data_and_headers` (needs `MostbetService`, which has no
counterpart anywhere in this codebase — Mostbet integration was out of
scope for the whole migration, not just this feature).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_brand_bot_callback_url
from app.db.tenant_celery import send_task
from app.models.tenant import (
    Merchant,
    MerchantBalance,
    MerchantPaymentMethod,
    Settlements,
    Transaction,
    UserConfig,
)

logger = logging.getLogger(__name__)

SEND_MESSAGE_TO_MERCHANT_TASK = "api_mediator.business_logic.services.callbacks.send_message_to_merchant"


async def send_transaction_callbacks(
    session: AsyncSession, *, brand_id: str, transaction_ids: list[int]
) -> dict[int, str]:
    """Returns `{transaction_id: outcome}`. `outcome` is one of:
    "queued", "skipped: no callback_url", "skipped: no_callback on merchant's
    payment method", "not found", or an error message — mirroring the
    per-transaction checks source's Celery task itself does before sending,
    surfaced synchronously here instead of only in the worker's logs, so the
    operator gets an answer immediately rather than having to go check.
    """
    results: dict[int, str] = {}
    stmt = (
        select(Transaction)
        .where(Transaction.id.in_(transaction_ids))
        .options(selectinload(Transaction.payment_method_company))
    )
    transactions = {t.id: t for t in (await session.execute(stmt)).scalars().all()}

    for transaction_id in transaction_ids:
        transaction = transactions.get(transaction_id)
        if transaction is None:
            results[transaction_id] = "not found"
            continue
        if not transaction.callback_url:
            results[transaction_id] = "skipped: no callback_url"
            continue

        mpm_stmt = select(MerchantPaymentMethod).where(
            MerchantPaymentMethod.merchant_id == transaction.merchant_id,
            MerchantPaymentMethod.payment_method_id == transaction.payment_method_company.payment_method_id,
        )
        merchant_payment_method = (await session.execute(mpm_stmt)).scalar_one_or_none()
        if merchant_payment_method is not None and merchant_payment_method.no_callback:
            results[transaction_id] = "skipped: no_callback on merchant's payment method"
            continue

        logger.info(
            "Callback будет отправлен мерчанту (async, через Celery): "
            "transaction_id=%s callback_url=%s",
            transaction_id,
            transaction.callback_url,
        )
        try:
            await send_task(brand_id, SEND_MESSAGE_TO_MERCHANT_TASK, args=[str(transaction_id)])
            results[transaction_id] = "queued"
        except Exception as exc:  # noqa: BLE001 — surfaced per-row to the caller, not raised
            logger.error("Не удалось поставить в очередь коллбэк для transaction_id=%s: %s", transaction_id, exc)
            results[transaction_id] = f"error: {exc}"

    return results


async def send_settlement_callbacks(
    session: AsyncSession, *, brand_id: str, settlement_ids: list[int]
) -> dict[int, str]:
    """Returns `{settlement_id: outcome}` — "sent", "skipped: no tg_id",
    "not found", or an error message. Port of
    `CallbacksService.send_message_to_tg_user`, called synchronously (as
    source does — it's not a Celery task there either)."""
    results: dict[int, str] = {}
    bot_callback_url = get_brand_bot_callback_url(brand_id)

    stmt = (
        select(Settlements)
        .where(Settlements.id.in_(settlement_ids))
        .options(
            selectinload(Settlements.balance_merchant).selectinload(MerchantBalance.merchant).selectinload(Merchant.user)
        )
    )
    settlements = {s.id: s for s in (await session.execute(stmt)).scalars().all()}

    for settlement_id in settlement_ids:
        settlement = settlements.get(settlement_id)
        if settlement is None:
            results[settlement_id] = "not found"
            continue
        if not settlement.tg_id:
            results[settlement_id] = "skipped: no tg_id"
            continue
        if not bot_callback_url:
            results[settlement_id] = "error: BOT_CALLBACK_URL not configured for this brand"
            continue

        data = await _build_settlement_callback_data(session, settlement)
        logger.info("SEND CALLBACK TO tg_id=%s URL=%s DATA=%s", settlement.tg_id, bot_callback_url, data)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(bot_callback_url, json=data)
            if response.status_code == 200:
                results[settlement_id] = "sent"
            else:
                results[settlement_id] = f"error: bot responded {response.status_code}"
        except httpx.HTTPError as exc:
            logger.error("Ошибка при отправке коллбэка на URL: %s. settlement_id=%s Error: %s", bot_callback_url, settlement_id, exc)
            results[settlement_id] = f"error: {exc}"

    return results


async def _build_settlement_callback_data(session: AsyncSession, settlement: Settlements) -> dict[str, Any]:
    """Port of `CallbacksService.create_callback_settlement_data`."""
    merchant_balance = settlement.balance_merchant
    merchant = merchant_balance.merchant

    config_stmt = select(UserConfig).where(UserConfig.user_id == merchant.user_id)
    user_config = (await session.execute(config_stmt)).scalar_one_or_none()
    is_exchanger = bool(user_config and user_config.enable_usdt_exchanger)

    if is_exchanger:
        balance_usdt = float(merchant_balance.balance_usdt)
    else:
        balance_usdt = float(merchant_balance.balance) / float(settlement.conversion_rate)

    return {
        "status": settlement.status,
        "amount": float(settlement.amount),
        "commission": float(settlement.commission) if settlement.commission is not None else None,
        "conversion_rate": float(settlement.conversion_rate) if settlement.conversion_rate is not None else None,
        "amount_in_usdt": float(settlement.amount_in_usdt),
        "wallet": settlement.wallet,
        "tracker_link": settlement.tracker_link,
        "final_amount": float(settlement.final_amount) if settlement.final_amount is not None else None,
        "final_amount_in_usdt": float(settlement.final_amount_in_usdt) if settlement.final_amount_in_usdt is not None else None,
        "transaction_id": settlement.transaction_id,
        "tg_id": settlement.tg_id,
        "balance": float(merchant_balance.balance_usdt) if is_exchanger else float(merchant_balance.balance),
        "balance_usdt": round(balance_usdt, 2),
    }
