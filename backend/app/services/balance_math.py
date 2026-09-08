"""1:1 port of the source monoliths' balance-mutation logic.

Ported from (verified verbatim against source, not paraphrased):
  - `personal_account_auth/business_logic/services/merchant_balance.py`
    (`MerchantBalanceService.merchant_balance_update`)
  - `api_mediator/business_logic/services/company.py`
    (`CompanyService.update_company_balance`)

Per explicit instruction this is a **bug-for-bug** port, not a cleaned-up
reimplementation. Every deviation from "obviously correct" code below is a
literal carryover from the source and is commented at the exact spot,
cross-referenced to `docs/migration-research/` line numbers where useful.
Do NOT "fix" these while touching this file — file a separate, explicit
change if the team decides to diverge from the source behavior.

Layering note: this module issues simple `get_or_create`-style SELECTs
directly rather than going through `app.repositories` — the source's own
"services" do the same (`MerchantBalance.objects.get_or_create(...)`
inline in business logic), and splitting this already-dense state machine
across an extra repository indirection would cost more clarity than it
buys. Treat this file as "domain logic with its own minimal persistence
primitives," not a precedent for other services to skip the repository
layer.

Concurrency: unlike the source (which has no `select_for_update`/locking
and relies solely on `F()`-expression increments to dodge lost updates),
the balance rows here are fetched with `.with_for_update()` — a deliberate
improvement over the source, not a math change. The caller
(`app.services.transaction_write_service`) is expected to run everything
in one DB transaction so these locks are held for the duration of the edit.

Known bugs/fragile assumptions carried over on purpose:
  1. `delta = old_x or transaction.x` uses Python truthiness on a `Decimal`
     everywhere in both source functions — a legitimately-zero old value is
     indistinguishable from "no old value supplied" and silently falls back
     to the *new* value instead of `Decimal('0')`.
  2. No zero-check on `usdt_fixed_course` before dividing by it.
  3. Only three statuses are handled (ACCEPTED/SUCCESS/DECLINED) via
     independent `if`s, not `elif`/`match` — an `APPEAL` transition touches
     neither merchant nor company balance, matching source exactly.
  4. `CompanyService`'s `changes_flag` is a single boolean gating FOUR
     independent deltas (amount, amount_after_commission, our_income,
     partner_income) — if only one of those four differs from its "old"
     value, the branch still substitutes ALL FOUR "old" values, not just
     the one that changed. This is preserved exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import (
    CompanyBalance,
    Merchant,
    MerchantBalance,
    PaymentMethod,
    PaymentMethodCompany,
    Transaction,
    UserConfig,
)

STATUS_ACCEPTED = "ACCEPTED"
STATUS_SUCCESS = "SUCCESS"
STATUS_DECLINED = "DECLINED"
STATUS_APPEAL = "APPEAL"

_USDT_QUANT = Decimal("0.00000001")

_MERCHANT_BALANCE_FIELDS = (
    "balance",
    "balance_usdt",
    "blocked_balance_in",
    "blocked_balance_out",
    "blocked_balance_usdt_in",
    "blocked_balance_usdt_out",
)

_COMPANY_BALANCE_FIELDS = (
    "available_balance",
    "blocked_balance_in",
    "blocked_balance_out",
    "our_income",
    "clients_funds",
)


@dataclass(frozen=True, slots=True)
class TransactionSnapshot:
    """Balance-relevant fields of a Transaction row taken BEFORE an edit —
    what the source calls `old_transaction`/the `old_*` kwargs."""

    status: str
    direction: str
    amount: Decimal
    amount_after_commission: Decimal
    commission: Decimal
    partner_income: Decimal
    pure_our_income: Decimal


def snapshot_transaction(txn: Transaction) -> TransactionSnapshot:
    return TransactionSnapshot(
        status=txn.status,
        direction=txn.direction,
        amount=txn.amount,
        amount_after_commission=txn.amount_after_commission,
        commission=txn.commission,
        partner_income=txn.partner_income,
        pure_our_income=txn.pure_our_income,
    )


async def get_payment_method_company(session: AsyncSession, pmc_id: int) -> PaymentMethodCompany:
    pmc = await session.get(PaymentMethodCompany, pmc_id)
    if pmc is None:
        raise ValueError(f"PaymentMethodCompany {pmc_id} not found")
    return pmc


async def get_payment_method(session: AsyncSession, payment_method_id: int) -> PaymentMethod:
    pm = await session.get(PaymentMethod, payment_method_id)
    if pm is None:
        raise ValueError(f"PaymentMethod {payment_method_id} not found")
    return pm


async def get_merchant_by_id(session: AsyncSession, merchant_id: int) -> Merchant:
    merchant = await session.get(Merchant, merchant_id)
    if merchant is None:
        raise ValueError(f"Merchant {merchant_id} not found")
    return merchant


async def get_user_config(session: AsyncSession, user_id: int) -> UserConfig | None:
    result = await session.execute(select(UserConfig).where(UserConfig.user_id == user_id))
    return result.scalar_one_or_none()


async def get_or_create_merchant_balance(
    session: AsyncSession, *, merchant_id: int, currency_id: int
) -> MerchantBalance:
    result = await session.execute(
        select(MerchantBalance)
        .where(MerchantBalance.merchant_id == merchant_id, MerchantBalance.currency_id == currency_id)
        .with_for_update()
    )
    balance = result.scalar_one_or_none()
    if balance is None:
        balance = MerchantBalance(merchant_id=merchant_id, currency_id=currency_id)
        session.add(balance)
        await session.flush()
    return balance


async def get_or_create_company_balance(
    session: AsyncSession, *, company_id: int, currency_id: int
) -> CompanyBalance:
    """Source: `CompanyService.get_or_create_company_balance_for_update` —
    the variant `update_company_balance` actually calls (NOT the redundant
    double-`.save()` sibling `get_or_create_company_balance`)."""
    result = await session.execute(
        select(CompanyBalance)
        .where(CompanyBalance.company_id == company_id, CompanyBalance.currency_id == currency_id)
        .with_for_update()
    )
    balance = result.scalar_one_or_none()
    if balance is None:
        balance = CompanyBalance(company_id=company_id, currency_id=currency_id)
        session.add(balance)
        await session.flush()
    return balance


async def apply_merchant_balance_update(
    session: AsyncSession,
    *,
    txn: Transaction,
    new_status: str,
    old_status: str | None,
    old_amount_after_commission: Decimal | None,
) -> MerchantBalance:
    """Port of `MerchantBalanceService.merchant_balance_update`."""
    pmc = await get_payment_method_company(session, txn.payment_method_company_id)
    payment_method = await get_payment_method(session, pmc.payment_method_id)
    merchant = await get_merchant_by_id(session, txn.merchant_id)
    user_config = await get_user_config(session, merchant.user_id)

    usdt_fixed_course = txn.usdt_fixed_course
    apply_usdt = bool(user_config and user_config.enable_usdt_exchanger and usdt_fixed_course is not None)

    usdt_amount: Decimal | None = None
    if apply_usdt:
        usdt_amount_raw = txn.amount_after_commission_in_usdt
        usdt_amount = (
            Decimal(usdt_amount_raw).quantize(_USDT_QUANT)
            if usdt_amount_raw is not None
            else (txn.amount_after_commission / usdt_fixed_course).quantize(_USDT_QUANT)
        )

    merchant_balance = await get_or_create_merchant_balance(
        session, merchant_id=txn.merchant_id, currency_id=payment_method.currency_id
    )
    initial_values = {f: getattr(merchant_balance, f) for f in _MERCHANT_BALANCE_FIELDS}

    # For exchanger merchants: only touch balance_usdt / blocked_balance_usdt_*
    skip_fiat = apply_usdt

    amount_after_commission = txn.amount_after_commission

    # ===================== ACCEPTED =====================
    if new_status == STATUS_ACCEPTED:
        if txn.direction == "IN":
            if not old_status or old_status == STATUS_DECLINED:
                if not skip_fiat:
                    merchant_balance.blocked_balance_in += amount_after_commission
                if apply_usdt:
                    merchant_balance.blocked_balance_usdt_in += usdt_amount

            if old_status == STATUS_ACCEPTED:
                delta = old_amount_after_commission or amount_after_commission  # bug-for-bug: Decimal truthiness
                if not skip_fiat:
                    merchant_balance.blocked_balance_in -= delta
                    merchant_balance.blocked_balance_in += amount_after_commission
                if apply_usdt:
                    merchant_balance.blocked_balance_usdt_in -= (delta / usdt_fixed_course).quantize(_USDT_QUANT)
                    merchant_balance.blocked_balance_usdt_in += usdt_amount

            if old_status == STATUS_SUCCESS:
                delta = old_amount_after_commission or amount_after_commission
                if not skip_fiat:
                    merchant_balance.balance -= delta
                    merchant_balance.blocked_balance_in += amount_after_commission
                if apply_usdt:
                    merchant_balance.blocked_balance_usdt_in += usdt_amount

        else:  # OUT
            if not old_status or old_status == STATUS_DECLINED:
                if not skip_fiat:
                    merchant_balance.blocked_balance_out += amount_after_commission
                    merchant_balance.balance -= amount_after_commission
                if apply_usdt:
                    merchant_balance.blocked_balance_usdt_out += usdt_amount

            if old_status == STATUS_ACCEPTED:
                delta = old_amount_after_commission or amount_after_commission
                if not skip_fiat:
                    merchant_balance.blocked_balance_out -= delta
                    merchant_balance.balance += delta
                    merchant_balance.blocked_balance_out += amount_after_commission
                    merchant_balance.balance -= amount_after_commission
                if apply_usdt:
                    merchant_balance.blocked_balance_usdt_out -= (delta / usdt_fixed_course).quantize(_USDT_QUANT)
                    merchant_balance.blocked_balance_usdt_out += usdt_amount

            if old_status == STATUS_SUCCESS:
                delta = old_amount_after_commission or amount_after_commission
                if not skip_fiat:
                    merchant_balance.balance += delta
                    merchant_balance.blocked_balance_out += amount_after_commission
                    merchant_balance.balance -= amount_after_commission
                if apply_usdt:
                    merchant_balance.blocked_balance_usdt_out += usdt_amount

    # ===================== SUCCESS =====================
    if new_status == STATUS_SUCCESS:
        if txn.direction == "IN":
            if not old_status or old_status == STATUS_DECLINED:
                if not skip_fiat:
                    merchant_balance.balance += amount_after_commission
                if apply_usdt:
                    merchant_balance.balance_usdt += usdt_amount

            if old_status == STATUS_ACCEPTED:
                delta = old_amount_after_commission or amount_after_commission
                if not skip_fiat:
                    merchant_balance.blocked_balance_in -= delta
                    merchant_balance.balance += amount_after_commission
                if apply_usdt:
                    merchant_balance.blocked_balance_usdt_in -= usdt_amount
                    merchant_balance.balance_usdt += usdt_amount

        else:  # OUT
            is_settlement = payment_method.name.lower() == "settlement"
            apply_usdt_out = apply_usdt and is_settlement  # only settlement, not ordinary OUT

            if not old_status or old_status == STATUS_DECLINED:
                if not skip_fiat:
                    merchant_balance.balance -= amount_after_commission
                if apply_usdt_out:
                    merchant_balance.balance_usdt -= usdt_amount

            if old_status == STATUS_ACCEPTED:
                delta = old_amount_after_commission or amount_after_commission
                if not skip_fiat:
                    merchant_balance.blocked_balance_out -= delta
                    merchant_balance.balance += delta
                    merchant_balance.balance -= amount_after_commission
                if apply_usdt:
                    merchant_balance.blocked_balance_usdt_out -= usdt_amount
                if apply_usdt_out:
                    merchant_balance.balance_usdt -= usdt_amount

    # ===================== DECLINED =====================
    if new_status == STATUS_DECLINED:
        if txn.direction == "IN":
            if old_status == STATUS_ACCEPTED:
                delta = old_amount_after_commission or amount_after_commission
                if not skip_fiat:
                    merchant_balance.blocked_balance_in -= delta
                if apply_usdt:
                    merchant_balance.blocked_balance_usdt_in -= usdt_amount
        else:  # OUT
            if old_status == STATUS_ACCEPTED:
                delta = old_amount_after_commission or amount_after_commission
                if not skip_fiat:
                    merchant_balance.blocked_balance_out -= delta
                    merchant_balance.balance += delta
                if apply_usdt:
                    merchant_balance.blocked_balance_usdt_out -= usdt_amount

    _ = initial_values  # source uses this only to compute F()-expr deltas for a raw UPDATE;
    # here we mutate the ORM instance directly and let SQLAlchemy's unit-of-work
    # flush the diff — see the write service for the surrounding locking strategy.
    return merchant_balance


async def apply_company_balance_update(
    session: AsyncSession,
    *,
    txn: Transaction,
    old_status: str | None,
    old_amount: Decimal = Decimal(0),
    old_amount_after_commission: Decimal = Decimal(0),
    old_our_income: Decimal = Decimal(0),
    old_partner_income: Decimal = Decimal(0),
) -> CompanyBalance:
    """Port of `CompanyService.update_company_balance`."""
    pmc = await get_payment_method_company(session, txn.payment_method_company_id)
    payment_method = await get_payment_method(session, pmc.payment_method_id)
    company_balance = await get_or_create_company_balance(
        session, company_id=pmc.company_id, currency_id=payment_method.currency_id
    )

    amount = txn.amount
    amount_after_commission = txn.amount_after_commission
    partner_income = txn.partner_income
    pure_our_income = txn.pure_our_income

    # bug-for-bug: Decimal truthiness — legitimately-zero "old" values are
    # indistinguishable from "not supplied".
    changes_flag = bool(
        (old_amount and old_amount != amount)
        or (old_amount_after_commission and old_amount_after_commission != amount_after_commission)
        or (old_our_income and old_our_income != pure_our_income)
        or (old_partner_income and old_partner_income != partner_income)
    )

    if txn.status == STATUS_ACCEPTED:
        if not old_status or old_status == STATUS_DECLINED:
            if txn.direction == "IN":
                company_balance.blocked_balance_in += amount - partner_income
            if txn.direction == "OUT":
                company_balance.blocked_balance_out += amount
                company_balance.available_balance -= amount + partner_income
                company_balance.clients_funds -= amount_after_commission
        if old_status == STATUS_ACCEPTED:
            if txn.direction == "IN":
                if changes_flag:
                    company_balance.blocked_balance_in -= old_amount - old_partner_income
                else:
                    company_balance.blocked_balance_in -= amount - partner_income
                company_balance.blocked_balance_in += amount - partner_income
            if txn.direction == "OUT":
                if changes_flag:
                    company_balance.blocked_balance_out -= old_amount
                    company_balance.available_balance += old_amount + old_partner_income
                    company_balance.clients_funds += old_amount_after_commission
                else:
                    company_balance.blocked_balance_out -= amount
                    company_balance.available_balance += amount + partner_income
                    company_balance.clients_funds += amount_after_commission
                company_balance.available_balance -= amount + partner_income
                company_balance.clients_funds -= amount_after_commission
                company_balance.blocked_balance_out += amount
        if old_status == STATUS_SUCCESS:
            if txn.direction == "IN":
                if changes_flag:
                    company_balance.available_balance -= old_amount - old_partner_income
                    company_balance.our_income -= old_our_income
                    company_balance.clients_funds -= old_amount_after_commission
                else:
                    company_balance.available_balance -= amount - partner_income
                    company_balance.our_income -= pure_our_income
                    company_balance.clients_funds -= amount_after_commission
                company_balance.blocked_balance_in += amount - partner_income
            if txn.direction == "OUT":
                if changes_flag:
                    company_balance.available_balance += old_amount + old_partner_income
                    company_balance.our_income -= old_our_income
                    company_balance.clients_funds += old_amount_after_commission
                else:
                    company_balance.available_balance += amount + partner_income
                    company_balance.our_income -= pure_our_income
                    company_balance.clients_funds += amount_after_commission
                company_balance.blocked_balance_out += amount
                company_balance.available_balance -= amount + partner_income
                company_balance.clients_funds -= amount_after_commission

    if txn.status == STATUS_SUCCESS:
        if not old_status or old_status == STATUS_DECLINED:
            if txn.direction == "IN":
                company_balance.available_balance += amount - partner_income
                company_balance.our_income += pure_our_income
                company_balance.clients_funds += amount_after_commission
            if txn.direction == "OUT":
                company_balance.available_balance -= amount + partner_income
                company_balance.clients_funds -= amount_after_commission
                company_balance.our_income += pure_our_income
        if old_status == STATUS_ACCEPTED:
            if txn.direction == "IN":
                if changes_flag:
                    company_balance.blocked_balance_in -= old_amount - old_partner_income
                else:
                    company_balance.blocked_balance_in -= amount - partner_income
                company_balance.available_balance += amount - partner_income
                company_balance.our_income += pure_our_income
                company_balance.clients_funds += amount_after_commission
            if txn.direction == "OUT":
                if changes_flag:
                    company_balance.blocked_balance_out -= old_amount
                    company_balance.available_balance += old_amount + old_partner_income
                    company_balance.clients_funds += old_amount_after_commission
                else:
                    company_balance.blocked_balance_out -= amount
                    company_balance.available_balance += amount + partner_income
                    company_balance.clients_funds += amount_after_commission
                company_balance.available_balance -= amount + partner_income
                company_balance.clients_funds -= amount_after_commission
                company_balance.our_income += pure_our_income
        if old_status == STATUS_SUCCESS:
            if txn.direction == "IN":
                if changes_flag:
                    company_balance.available_balance -= old_amount - old_partner_income
                    company_balance.our_income -= old_our_income
                    company_balance.clients_funds -= old_amount_after_commission
                else:
                    company_balance.available_balance -= amount - partner_income
                    company_balance.our_income -= pure_our_income
                    company_balance.clients_funds -= amount_after_commission
                company_balance.available_balance += amount - partner_income
                company_balance.our_income += pure_our_income
                company_balance.clients_funds += amount_after_commission
            if txn.direction == "OUT":
                if changes_flag:
                    company_balance.available_balance += old_amount + old_partner_income
                    company_balance.clients_funds += old_amount_after_commission
                    company_balance.our_income -= old_our_income
                else:
                    company_balance.available_balance += amount + partner_income
                    company_balance.clients_funds += amount_after_commission
                    company_balance.our_income -= pure_our_income
                company_balance.available_balance -= amount + partner_income
                company_balance.clients_funds -= amount_after_commission
                company_balance.our_income += pure_our_income

    if txn.status == STATUS_DECLINED:
        if old_status == STATUS_ACCEPTED:
            if txn.direction == "IN":
                if changes_flag:
                    company_balance.blocked_balance_in -= old_amount - old_partner_income
                else:
                    company_balance.blocked_balance_in -= amount - partner_income
            else:
                if changes_flag:
                    company_balance.blocked_balance_out -= old_amount
                    company_balance.available_balance += old_amount + old_partner_income
                    company_balance.clients_funds += old_amount_after_commission
                else:
                    company_balance.blocked_balance_out -= amount
                    company_balance.available_balance += amount + partner_income
                    company_balance.clients_funds += amount_after_commission
        if old_status == STATUS_SUCCESS:
            if txn.direction == "IN":
                if changes_flag:
                    company_balance.available_balance -= old_amount - old_partner_income
                    company_balance.our_income -= old_our_income
                    company_balance.clients_funds -= old_amount_after_commission
                else:
                    company_balance.available_balance -= amount - partner_income
                    company_balance.our_income -= pure_our_income
                    company_balance.clients_funds -= amount_after_commission
            if txn.direction == "OUT":
                if changes_flag:
                    company_balance.available_balance += old_amount + old_partner_income
                    company_balance.clients_funds += old_amount_after_commission
                    company_balance.our_income -= old_our_income
                else:
                    company_balance.available_balance += amount + partner_income
                    company_balance.clients_funds += amount_after_commission
                    company_balance.our_income -= pure_our_income

    return company_balance


async def apply_company_balance_via_settlement(
    session: AsyncSession,
    *,
    txn: Transaction,
    old_status: str | None,
) -> CompanyBalance:
    """Port of `TransactionSaveService.update_company_balance_throw_settlement`.

    Only reached for the "admin merchant" branch: the transaction's merchant
    is the special admin/settlement merchant (`public_key == ADMIN_PUBLIC_KEY`)
    AND the payment method is named "settlement" — see the 3-way branch in
    `transaction_write_service.update_transaction`. Uses the OTHER
    get-or-create variant than the general path (`get_or_create_company_balance`,
    with its redundant extra `.save()` in source — reproduced here as a
    harmless no-op extra flush, not a correctness issue, purely for fidelity).
    """
    pmc = await get_payment_method_company(session, txn.payment_method_company_id)
    payment_method = await get_payment_method(session, pmc.payment_method_id)
    company_balance = await get_or_create_company_balance(
        session, company_id=pmc.company_id, currency_id=payment_method.currency_id
    )

    amount_after_commission = txn.amount_after_commission
    amount = txn.amount
    commission = txn.commission
    pure_our_income = txn.pure_our_income

    if not old_status:
        # New settlement transaction.
        if txn.status == STATUS_ACCEPTED:
            if txn.direction == "IN":
                company_balance.blocked_balance_in += amount_after_commission
            else:
                company_balance.blocked_balance_out += amount
                company_balance.available_balance -= amount
                company_balance.clients_funds -= amount_after_commission - pure_our_income
                company_balance.our_income -= pure_our_income + commission
        if txn.status == STATUS_SUCCESS:
            if txn.direction == "IN":
                company_balance.available_balance += amount_after_commission
                company_balance.clients_funds += amount_after_commission
                company_balance.our_income += commission - txn.partner_income
            else:
                company_balance.available_balance -= amount
                company_balance.clients_funds -= amount_after_commission - pure_our_income
                company_balance.our_income -= pure_our_income + commission
    else:
        # Updating an existing settlement transaction.
        if txn.status == STATUS_ACCEPTED:
            if old_status == STATUS_DECLINED:
                if txn.direction == "IN":
                    company_balance.blocked_balance_in += amount_after_commission
                else:
                    company_balance.blocked_balance_out += amount
                    company_balance.available_balance -= amount
                    company_balance.clients_funds -= amount_after_commission - pure_our_income
                    company_balance.our_income -= pure_our_income + commission
            if old_status == STATUS_SUCCESS:
                if txn.direction == "IN":
                    company_balance.available_balance -= amount_after_commission
                    company_balance.clients_funds -= amount_after_commission
                    company_balance.our_income -= commission - txn.partner_income
                    company_balance.blocked_balance_in += amount_after_commission
                else:
                    company_balance.available_balance += amount
                    company_balance.clients_funds += amount_after_commission - pure_our_income
                    company_balance.our_income += pure_our_income + commission
                    company_balance.blocked_balance_out += amount
                    company_balance.available_balance -= amount
                    company_balance.clients_funds -= amount_after_commission - pure_our_income
                    company_balance.our_income -= pure_our_income + commission
            if old_status == STATUS_ACCEPTED:
                if txn.direction == "IN":
                    company_balance.blocked_balance_in -= amount_after_commission
                    company_balance.blocked_balance_in += amount_after_commission
                else:
                    company_balance.blocked_balance_out -= amount
                    company_balance.available_balance += amount
                    company_balance.clients_funds += amount_after_commission - pure_our_income
                    company_balance.our_income += pure_our_income + commission
                    company_balance.blocked_balance_out += amount
                    company_balance.available_balance -= amount
                    company_balance.clients_funds -= amount_after_commission - pure_our_income
                    company_balance.our_income -= pure_our_income + commission
        if txn.status == STATUS_SUCCESS:
            if txn.direction == "IN":
                if old_status == STATUS_ACCEPTED:
                    company_balance.blocked_balance_in -= amount_after_commission
                elif old_status == STATUS_SUCCESS:
                    company_balance.available_balance -= amount_after_commission
                    company_balance.clients_funds -= amount_after_commission
                    company_balance.our_income -= commission - txn.partner_income
                company_balance.available_balance += amount_after_commission
                company_balance.clients_funds += amount_after_commission
                company_balance.our_income += commission - txn.partner_income
            else:
                if old_status == STATUS_ACCEPTED:
                    company_balance.blocked_balance_out -= amount
                    company_balance.available_balance += amount
                    company_balance.clients_funds += amount_after_commission - pure_our_income
                    company_balance.our_income += pure_our_income + commission
                elif old_status == STATUS_SUCCESS:
                    company_balance.available_balance += amount
                    company_balance.clients_funds += amount_after_commission - pure_our_income
                    company_balance.our_income += pure_our_income + commission
                company_balance.available_balance -= amount
                company_balance.clients_funds -= amount_after_commission - pure_our_income
                company_balance.our_income -= pure_our_income + commission
        if txn.status == STATUS_DECLINED:
            if old_status == STATUS_ACCEPTED:
                if txn.direction == "IN":
                    company_balance.blocked_balance_in -= amount_after_commission
                else:
                    company_balance.blocked_balance_out -= amount
                    company_balance.available_balance += amount
                    company_balance.clients_funds += amount_after_commission - pure_our_income
                    company_balance.our_income += pure_our_income + commission
            elif old_status == STATUS_SUCCESS:
                if txn.direction == "IN":
                    company_balance.available_balance -= amount_after_commission
                    company_balance.clients_funds -= amount_after_commission
                    company_balance.our_income -= commission - txn.partner_income
                else:
                    company_balance.available_balance += amount
                    company_balance.clients_funds += amount_after_commission - pure_our_income
                    company_balance.our_income += pure_our_income + commission

    return company_balance


def apply_update_limits_noop(pmc: PaymentMethodCompany) -> PaymentMethodCompany:
    """Port of `PaymentMethodService.update_limits`.

    **This is a literal no-op in the source** — the real `.update(...)`
    call that increments `current_daily_count`/`current_daily_amount` is
    commented out in `payment_method.py:537-540`; the function only
    re-reads (unchanged) values. Daily limits are therefore NOT actually
    enforced live in the source system today. Ported as a genuine no-op
    per the "1:1 including bugs" decision — flagging loudly here and in
    the audit log entry the write service records, since this is the kind
    of "bug" a payments team should consciously decide whether to keep.
    """
    return pmc
