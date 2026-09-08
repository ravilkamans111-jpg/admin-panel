"""Port of `TransactionAdmin.save_model` -> `Transaction.save(from_admin=True, ...)`
-> `TransactionSaveService.pre_save_from_admin_transaction`.

The source's 3-way branch (verbatim, from `transaction_save.py`):
  1. `is_admin_merchant` (merchant.public_key == ADMIN_PUBLIC_KEY AND the
     payment method is named "settlement") -> `update_company_balance_throw_settlement`
     only (no merchant balance touched at all in this branch).
  2. Company is named "ampay" AND payment method is "settlement" ->
     `MerchantBalanceService.merchant_balance_update` only (no company
     balance touched).
  3. General case -> BOTH `merchant_balance_update` AND
     `update_company_balance`, then `update_limits` (a documented no-op —
     see `balance_math.apply_update_limits_noop`).

Deliberately NOT ported (see gaps flagged by the source extraction and the
project's phased-write-path plan):
  - Conversion statistics update — the source itself treats this as
    non-fatal (broad `except Exception: log and continue`, transaction
    save still succeeds), and its `get_or_create` path has an
    unread-model-defaults gap (`conversion_statistic/models.py` was not in
    the verified extraction). Add once that gap is closed.
  - Redis cache invalidation / Celery merchant callbacks — confirmed NOT
    part of this save path in source (they belong to `PaymentMethodCompany`/
    `MerchantPaymentMethod.save()` and a separate bulk admin action,
    respectively). Out of scope for editing a Transaction/Settlement.

Unlike the source, this module wraps the whole edit in one DB transaction
with row-level locks (`with_for_update()` on the Transaction and every
balance row touched) — the source has no locking at all here, relying
solely on `F()`-expression increments. This is a deliberate concurrency
improvement, not a change to the computed values.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_brand_admin_public_key
from app.core.exceptions import RecordNotFoundError
from app.models.tenant import (
    Company,
    Merchant,
    MerchantPaymentMethod,
    PaymentMethod,
    PaymentMethodCompany,
    Transaction,
)
from app.registry.admin_models import get_config
from app.repositories.admin_repository import row_to_dict
from app.services.balance_math import (
    TransactionSnapshot,
    apply_company_balance_update,
    apply_company_balance_via_settlement,
    apply_merchant_balance_update,
    snapshot_transaction,
)

# Single source of truth is the registry entry (`app.registry.admin_models`,
# key="transactions") — the frontend reads the same list via `/admin/schema`
# to decide which fields to render as editable, so it must never drift from
# what this service actually accepts.
_transactions_config = get_config("transactions")
assert _transactions_config is not None, "transactions must be registered in app.registry.admin_models"
EDITABLE_FIELDS = tuple(_transactions_config.editable_fields)


@dataclass(frozen=True, slots=True)
class CommissionContext:
    """Rates needed to reproduce the source's live commission preview —
    port of `TransactionAdminForm.__init__`'s `data-merchant-commission`/
    `data-partner-commission`/`data-direction` attributes, and the exact
    formula from `static/admin/js/transaction_changes.js`:

        commission = amount * merchant_personal_rate / 100
        partner_income = amount * partner_rate / 100
        pure_our_income = commission - partner_income
        amount_after_commission = amount - commission if direction == 'IN' else amount + commission

    Source only attaches this JS at all if a `MerchantPaymentMethod` row
    exists for (merchant, payment_method) — if not, the whole `try` block
    in `TransactionAdminForm.__init__` silently returns and no live calc
    ever runs. `get_commission_context` mirrors that: returns `None` when
    there's no matching `MerchantPaymentMethod`, and the frontend should
    treat that as "no live recalculation available" rather than guessing.
    """

    merchant_personal_rate: Decimal
    partner_rate: Decimal
    direction: str


async def get_commission_context(session: AsyncSession, txn: Transaction) -> CommissionContext | None:
    pmc = await session.get(PaymentMethodCompany, txn.payment_method_company_id)
    if pmc is None:
        return None
    result = await session.execute(
        select(MerchantPaymentMethod).where(
            MerchantPaymentMethod.merchant_id == txn.merchant_id,
            MerchantPaymentMethod.payment_method_id == pmc.payment_method_id,
        )
    )
    merchant_payment_method = result.scalar_one_or_none()
    if merchant_payment_method is None:
        return None
    return CommissionContext(
        merchant_personal_rate=merchant_payment_method.personal_rate,
        partner_rate=pmc.partner_rate or Decimal(0),
        direction=txn.direction,
    )


async def update_transaction(
    session: AsyncSession, *, brand_id: str, pk: int, values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Applies an admin edit to a Transaction, running the same balance
    side effects the source treats as inseparable from the save. Returns
    (before, after) row snapshots for the audit log. Caller commits."""
    result = await session.execute(select(Transaction).where(Transaction.id == pk).with_for_update())
    txn = result.scalar_one_or_none()
    if txn is None:
        raise RecordNotFoundError(f"Transaction {pk} not found")

    before = row_to_dict(txn)
    old_snapshot = snapshot_transaction(txn)

    unknown = [f for f in values if f not in EDITABLE_FIELDS]
    if unknown:
        raise ValueError(f"Not editable on Transaction: {unknown}")
    for field_name, value in values.items():
        setattr(txn, field_name, value)

    # Always a real snapshot here — this write path only edits existing rows.
    await run_save_side_effects(session, brand_id=brand_id, txn=txn, old_snapshot=old_snapshot)

    await session.flush()
    after = row_to_dict(txn)
    return before, after


async def run_save_side_effects(
    session: AsyncSession, *, brand_id: str, txn: Transaction, old_snapshot: TransactionSnapshot | None
) -> None:
    """Runs the balance-mutation side effects source treats as inseparable
    from saving a Transaction — the 3-way branch documented in the module
    docstring. `old_snapshot=None` means "this is a brand-new Transaction"
    (matches source's `old_transaction=None` case, e.g. a Settlement
    creating its linked Transaction for the first time — see
    `app.services.settlement_write_service`); a real snapshot means "this
    row already existed with these balance-relevant field values" (an edit,
    whether direct or via a Settlement update).
    """
    pmc = await session.get(PaymentMethodCompany, txn.payment_method_company_id)
    if pmc is None:
        raise ValueError(f"PaymentMethodCompany {txn.payment_method_company_id} not found")
    payment_method = await session.get(PaymentMethod, pmc.payment_method_id)
    if payment_method is None:
        raise ValueError(f"PaymentMethod {pmc.payment_method_id} not found")
    company = await session.get(Company, pmc.company_id)
    if company is None:
        raise ValueError(f"Company {pmc.company_id} not found")
    merchant = await session.get(Merchant, txn.merchant_id)
    if merchant is None:
        raise ValueError(f"Merchant {txn.merchant_id} not found")

    admin_public_key = get_brand_admin_public_key(brand_id)
    is_settlement_method = payment_method.name.lower() == "settlement"
    is_admin_merchant = bool(
        admin_public_key and merchant.public_key == admin_public_key and is_settlement_method
    )

    old_status = old_snapshot.status if old_snapshot else None
    old_amount_after_commission = old_snapshot.amount_after_commission if old_snapshot else None

    if is_admin_merchant:
        await apply_company_balance_via_settlement(session, txn=txn, old_status=old_status)
    elif company.name.lower() == "ampay" and is_settlement_method:
        await apply_merchant_balance_update(
            session,
            txn=txn,
            new_status=txn.status,
            old_status=old_status,
            old_amount_after_commission=old_amount_after_commission,
        )
    else:
        await apply_merchant_balance_update(
            session,
            txn=txn,
            new_status=txn.status,
            old_status=old_status,
            old_amount_after_commission=old_amount_after_commission,
        )
        await apply_company_balance_update(
            session,
            txn=txn,
            old_status=old_status,
            old_amount=old_snapshot.amount if old_snapshot else Decimal(0),
            old_amount_after_commission=old_amount_after_commission if old_snapshot else Decimal(0),
            old_our_income=old_snapshot.pure_our_income if old_snapshot else Decimal(0),
            old_partner_income=old_snapshot.partner_income if old_snapshot else Decimal(0),
        )
        # `update_limits` is a documented no-op in source — not called at
        # all here since it has no observable effect (see balance_math.py).
