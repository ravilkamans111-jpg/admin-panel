"""Port of `SettlementsAdmin.save_model` -> `Settlements.save(old_settlement=...)`
-> `SettlementSaveService.pre_save_always_settlement` + `pre_save_from_admin_settlement`.

Source (verbatim, extracted and verified against `settlements_save.py` — see
`docs/migration-research/` and the scratchpad extraction report referenced
in the Group B write-path work): creating or editing a Settlement is NOT a
plain row write. It:

  1. Runs `pre_save_always_settlement` — recomputes `commission_amount`
     (a DTO-only value, not a persisted column — it only feeds the linked
     Transaction's `commission` field), `final_amount`, `final_amount_in_usdt`
     from raw inputs, and — this is the surprising part — OVERRIDES
     whichever `balance_merchant`/`balance_partner` the caller selected:
       - FROM_PARTNER/TO_PARTNER: `balance_merchant` is forced to the
         special admin/recipient merchant's balance (`ADMIN_PUBLIC_KEY`),
         regardless of what was submitted.
       - TO_MERCHANT/FROM_MERCHANT: `balance_partner` is forced to a
         `get_or_create_by_name('AmPay')` company's balance.
  2. Runs `pre_save_from_admin_settlement` — builds the fields for a linked
     Transaction row and delegates the actual balance-mutation side effects
     to the SAME `TransactionSaveService.pre_save_from_admin_transaction`
     used when editing a Transaction directly (ported as
     `app.services.transaction_write_service`) — a Settlement creates or
     updates a Transaction as an inseparable part of saving it, matched by
     the OLD transaction's `pk` (edit) or created fresh (`pk=None`, new).

Bug-for-bug per the same instruction as `balance_math.py`:
  - `direction`/`partner_income`/`pure_our_income` are only assigned inside
    mutually-exclusive `if` blocks covering all 4 `SettlementTypeChoices`
    values — an `UnboundLocalError` risk if that enum ever grows a 5th
    value, but currently safe. Preserved as-is.
  - Several `x or default` truthiness checks on `Decimal` (same class of
    bug as `balance_math.py` — a legitimate zero is indistinguishable from
    "not provided").
  - `settlement.our_funds = settlement.our_funds` / `settlement.balance_partner
    = settlement.balance_partner` self-assignments in source are literal
    no-ops — nothing to reproduce.

NOT verified against source (gap flagged by the extraction agent): the
exact query behind `PaymentMethodService.get_payment_method_company_by_method_currency_direction_company`
was not in the read scope. Implemented here as the most direct reading —
find the PaymentMethodCompany for this company whose PaymentMethod is named
"settlement", matching the given currency and direction. Verify against
source before relying on this for a real production create.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_brand_admin_public_key
from app.core.exceptions import RecordNotFoundError
from app.models.tenant import (
    Company,
    CompanyBalance,
    Merchant,
    MerchantBalance,
    PaymentMethod,
    PaymentMethodCompany,
    Settlements,
    Transaction,
)
from app.registry.admin_models import get_config
from app.repositories.admin_repository import row_to_dict
from app.services import transaction_write_service
from app.services.balance_math import (
    get_or_create_company_balance,
    get_or_create_merchant_balance,
    get_user_config,
    snapshot_transaction,
)

SETTL_FROM_PARTNER = "FROM_PARTNER"
SETTL_TO_MERCHANT = "TO_MERCHANT"
SETTL_FROM_MERCHANT = "FROM_MERCHANT"
SETTL_TO_PARTNER = "TO_PARTNER"

_USDT_QUANT = Decimal("0.00000001")
_SENDER_COMPANY_NAME = "AmPay"

_settlements_config = get_config("settlements")
assert _settlements_config is not None, "settlements must be registered in app.registry.admin_models"
EDITABLE_FIELDS = tuple(_settlements_config.editable_fields)
CREATE_ONLY_FIELDS = tuple(_settlements_config.creatable_fields)


@dataclass(frozen=True, slots=True)
class SettlementWriteResult:
    settlement_before: dict[str, Any] | None
    settlement_after: dict[str, Any]
    transaction_after: dict[str, Any]


async def get_merchant_by_public_key(session: AsyncSession, public_key: str) -> Merchant | None:
    result = await session.execute(select(Merchant).where(Merchant.public_key == public_key))
    return result.scalar_one_or_none()


async def get_or_create_company_by_name(session: AsyncSession, name: str) -> Company:
    result = await session.execute(select(Company).where(Company.name == name))
    company = result.scalar_one_or_none()
    if company is None:
        company = Company(name=name)
        session.add(company)
        await session.flush()
    return company


async def find_settlement_payment_method_company(
    session: AsyncSession, *, company_id: int, currency_id: int, direction: str
) -> PaymentMethodCompany | None:
    result = await session.execute(
        select(PaymentMethodCompany)
        .join(PaymentMethod, PaymentMethodCompany.payment_method_id == PaymentMethod.id)
        .where(
            PaymentMethodCompany.company_id == company_id,
            PaymentMethod.currency_id == currency_id,
            PaymentMethod.direction == direction,
            PaymentMethod.name.ilike("settlement"),
        )
    )
    return result.scalars().first()


async def create_or_update_settlement(
    session: AsyncSession, *, brand_id: str, pk: int | None, values: dict[str, Any]
) -> SettlementWriteResult:
    """`pk=None` -> create (source's `SettlementsAdmin.save_model` with
    `change=False`); `pk` given -> edit an existing settlement."""
    settlement_before: dict[str, Any] | None = None
    old_transaction: Transaction | None = None

    if pk is not None:
        result = await session.execute(select(Settlements).where(Settlements.id == pk).with_for_update())
        settlement = result.scalar_one_or_none()
        if settlement is None:
            raise RecordNotFoundError(f"Settlement {pk} not found")
        settlement_before = row_to_dict(settlement)
        if settlement.transaction_id:
            txn_result = await session.execute(
                select(Transaction).where(Transaction.tracker_id == settlement.transaction_id).with_for_update()
            )
            old_transaction = txn_result.scalar_one_or_none()

        unknown = [f for f in values if f not in EDITABLE_FIELDS]
        if unknown:
            raise ValueError(f"Not editable on Settlement: {unknown}")
        for field_name, value in values.items():
            setattr(settlement, field_name, value)
    else:
        allowed = set(EDITABLE_FIELDS) | set(CREATE_ONLY_FIELDS)
        unknown = [f for f in values if f not in allowed]
        if unknown:
            raise ValueError(f"Not a valid field on Settlement: {unknown}")
        missing = [f for f in CREATE_ONLY_FIELDS if f not in values]
        if missing:
            raise ValueError(f"Required on create: {missing}")
        now = datetime.now(UTC)
        settlement = Settlements(
            status=values.get("status", "ACCEPTED"),
            settl_type=values["settl_type"],
            balance_merchant_id=values["balance_merchant_id"],
            balance_partner_id=values["balance_partner_id"],
            amount=values["amount"],
            commission=values.get("commission"),
            our_funds=values.get("our_funds"),
            clients_funds=values.get("clients_funds"),
            conversion_rate=values.get("conversion_rate"),
            # NOT NULL on the real schema (verified against the source
            # migration — `SettlementsDTO.amount_in_usdt` is a required key
            # in source too, just typed `Decimal | None` there). Defaults to
            # 0 for the common non-exchanger settlement, matching how an
            # admin form would presumably pre-fill it.
            amount_in_usdt=values.get("amount_in_usdt", Decimal(0)),
            final_amount=values.get("final_amount"),
            final_amount_in_usdt=values.get("final_amount_in_usdt"),
            wallet=values.get("wallet"),
            tracker_link=values.get("tracker_link"),
            transaction_id=values.get("transaction_id"),
            tg_id=values.get("tg_id"),
            date_create=now,
            date_update=now,
        )
        session.add(settlement)
        await session.flush()

    commission_amount = await _apply_pre_save_always(session, brand_id=brand_id, settlement=settlement)
    transaction_after = await _apply_pre_save_from_admin(
        session,
        brand_id=brand_id,
        settlement=settlement,
        old_transaction=old_transaction,
        commission_amount=commission_amount,
    )

    await session.flush()
    settlement_after = row_to_dict(settlement)
    return SettlementWriteResult(
        settlement_before=settlement_before, settlement_after=settlement_after, transaction_after=transaction_after
    )


async def _apply_pre_save_always(session: AsyncSession, *, brand_id: str, settlement: Settlements) -> Decimal:
    """Port of `SettlementSaveService.pre_save_always_settlement`.

    Returns `commission_amount` (a DTO-only value in source, not persisted
    on the Settlements row — only fed into the linked Transaction's
    `commission` field by `_apply_pre_save_from_admin`)."""
    balance_partner = await session.get(CompanyBalance, settlement.balance_partner_id)
    if balance_partner is None:
        raise ValueError(f"CompanyBalance {settlement.balance_partner_id} not found")
    balance_merchant = await session.get(MerchantBalance, settlement.balance_merchant_id)
    if balance_merchant is None:
        raise ValueError(f"MerchantBalance {settlement.balance_merchant_id} not found")

    amount = settlement.amount
    amount_in_usdt = settlement.amount_in_usdt

    commission_amount = Decimal(0)

    if settlement.settl_type in (SETTL_FROM_PARTNER, SETTL_TO_PARTNER):
        admin_public_key = get_brand_admin_public_key(brand_id)
        if not admin_public_key:
            raise ValueError(
                "No ADMIN_PUBLIC_KEY configured for this brand — required for "
                f"{settlement.settl_type} settlements (see app.core.config.get_brand_admin_public_key)."
            )
        recipient = await get_merchant_by_public_key(session, admin_public_key)
        if recipient is None:
            raise ValueError("Admin/recipient merchant (ADMIN_PUBLIC_KEY) not found in this brand's DB")
        recipient_balance = await get_or_create_merchant_balance(
            session, merchant_id=recipient.id, currency_id=balance_partner.currency_id
        )
        settlement.balance_merchant_id = recipient_balance.id

        commission_rate = balance_partner.settlement_commission if not settlement.commission else settlement.commission
        settlement.commission = commission_rate
        commission_amount = amount * Decimal(commission_rate) / Decimal(100) if commission_rate else Decimal(0)
        commission_amount_in_usdt = (
            (amount_in_usdt * Decimal(commission_rate) / Decimal(100)) if (commission_rate and amount_in_usdt) else Decimal(0)
        )
        if not settlement.final_amount:
            settlement.final_amount = amount - commission_amount
        if not settlement.final_amount_in_usdt and amount_in_usdt is not None:
            settlement.final_amount_in_usdt = amount_in_usdt - commission_amount_in_usdt

    elif settlement.settl_type in (SETTL_TO_MERCHANT, SETTL_FROM_MERCHANT):
        merchant = await session.get(Merchant, balance_merchant.merchant_id)
        if merchant is None:
            raise ValueError(f"Merchant {balance_merchant.merchant_id} not found")
        user_config = await get_user_config(session, merchant.user_id)
        is_exchanger = bool(user_config and user_config.enable_usdt_exchanger)
        if is_exchanger and settlement.conversion_rate is None:
            settlement.conversion_rate = Decimal(1)

        sender = await get_or_create_company_by_name(session, _SENDER_COMPANY_NAME)
        sender_balance = await get_or_create_company_balance(
            session, company_id=sender.id, currency_id=balance_merchant.currency_id
        )
        settlement.balance_partner_id = sender_balance.id

        settlement.clients_funds = settlement.clients_funds or None
        settlement.our_funds = None
        commission_rate = balance_merchant.settlement_commission if not settlement.commission else settlement.commission
        settlement.commission = commission_rate
        commission_amount = amount * Decimal(commission_rate) / Decimal(100) if commission_rate else Decimal(0)
        commission_amount_in_usdt = (
            (amount_in_usdt * Decimal(commission_rate) / Decimal(100)) if (commission_rate and amount_in_usdt) else Decimal(0)
        )
        settlement.final_amount = amount - commission_amount
        if amount_in_usdt is not None:
            settlement.final_amount_in_usdt = amount_in_usdt - commission_amount_in_usdt

    await session.flush()
    return commission_amount


async def _apply_pre_save_from_admin(
    session: AsyncSession,
    *,
    brand_id: str,
    settlement: Settlements,
    old_transaction: Transaction | None,
    commission_amount: Decimal,
) -> dict[str, Any]:
    """Port of `SettlementSaveService.pre_save_from_admin_settlement`."""
    if settlement.settl_type in (SETTL_FROM_PARTNER, SETTL_TO_MERCHANT):
        direction = "OUT"
    elif settlement.settl_type in (SETTL_FROM_MERCHANT, SETTL_TO_PARTNER):
        direction = "IN"
    else:  # pragma: no cover - unreachable given SettlementTypeChoices has exactly these 4 values
        raise ValueError(f"Unknown settl_type: {settlement.settl_type}")

    if settlement.settl_type in (SETTL_FROM_PARTNER, SETTL_TO_PARTNER):
        partner_income = commission_amount or Decimal(0)
        pure_our_income = (settlement.our_funds - partner_income) if settlement.our_funds else Decimal(0) - partner_income
    else:  # TO_MERCHANT / FROM_MERCHANT
        partner_income = commission_amount or Decimal(0)
        pure_our_income = commission_amount or Decimal(0)

    if settlement.settl_type == SETTL_TO_MERCHANT:
        # Source, verbatim: this OVERWRITES the commission-discounted
        # `final_amount` that `_apply_pre_save_always` just computed, back
        # to the full `amount` — for TO_MERCHANT specifically, the
        # commission is computed (and fed into the linked Transaction's
        # `commission`/`partner_income`/`pure_our_income` fields) but never
        # actually subtracted from what ends up as `final_amount` /
        # `Transaction.amount_after_commission`. Confirmed against source,
        # not a porting bug — flag to the team if this looks wrong; it may
        # itself be an unintentional bug in the original that was never
        # caught because commission on TO_MERCHANT payouts is typically 0.
        settlement.final_amount = settlement.amount

    balance_merchant = await session.get(MerchantBalance, settlement.balance_merchant_id)
    balance_partner = await session.get(CompanyBalance, settlement.balance_partner_id)
    if balance_merchant is None or balance_partner is None:
        raise ValueError("balance_merchant/balance_partner must exist after pre_save_always_settlement")

    merchant = (
        await session.get(Merchant, old_transaction.merchant_id)
        if old_transaction
        else await session.get(Merchant, balance_merchant.merchant_id)
    )
    if merchant is None:
        raise ValueError("Merchant not found for settlement's linked transaction")

    user_config = await get_user_config(session, merchant.user_id)
    is_exchanger = direction == "OUT" and bool(user_config and user_config.enable_usdt_exchanger)

    conv_rate = settlement.conversion_rate
    if is_exchanger and conv_rate is None:
        conv_rate = Decimal(1)
    final_usdt = settlement.final_amount_in_usdt
    if is_exchanger and final_usdt is None and conv_rate and settlement.final_amount is not None:
        final_usdt = (settlement.final_amount / conv_rate).quantize(_USDT_QUANT)
    is_exchanger_settlement = is_exchanger and conv_rate is not None and final_usdt is not None
    usdt_fixed_course = conv_rate if is_exchanger_settlement else None
    amount_after_commission_in_usdt = final_usdt if is_exchanger_settlement else None

    if old_transaction is not None:
        txn = old_transaction
    else:
        payment_method_company = await find_settlement_payment_method_company(
            session, company_id=balance_partner.company_id, currency_id=balance_partner.currency_id, direction=direction
        )
        if payment_method_company is None:
            raise ValueError(
                "No settlement PaymentMethodCompany found for this company/currency/direction — "
                "see the module docstring: this lookup is not verified against source."
            )
        now = datetime.now(UTC)
        txn = Transaction(
            tracker_id=settlement.transaction_id or str(uuid.uuid4()),
            partner_system_id=str(uuid.uuid4()),
            merchant_system_id=str(uuid.uuid4()),
            merchant_client_id=str(uuid.uuid4()),
            merchant_id=merchant.id,
            payment_method_company_id=payment_method_company.id,
            status=settlement.status,
            direction=direction,
            date_create=now,
            date_update=now,
            amount=settlement.amount,
            commission=commission_amount or Decimal(0),
            partner_income=partner_income,
            pure_our_income=pure_our_income,
            amount_after_commission=settlement.final_amount,
            addition_info="СОЗДАНО ПРИ СЕТТЛМЕНТЕ.",
            usdt_fixed_course=usdt_fixed_course,
            amount_after_commission_in_usdt=amount_after_commission_in_usdt,
        )
        session.add(txn)
        await session.flush()
        settlement.transaction_id = txn.tracker_id

    old_snapshot = snapshot_transaction(txn) if old_transaction is not None else None

    if old_transaction is not None:
        txn.status = settlement.status
        txn.amount = settlement.amount
        txn.commission = commission_amount or Decimal(0)
        txn.partner_income = partner_income
        txn.pure_our_income = pure_our_income
        txn.amount_after_commission = settlement.final_amount
        txn.usdt_fixed_course = usdt_fixed_course
        txn.amount_after_commission_in_usdt = amount_after_commission_in_usdt

    await transaction_write_service.run_save_side_effects(
        session, brand_id=brand_id, txn=txn, old_snapshot=old_snapshot
    )

    await session.flush()
    return row_to_dict(txn)
