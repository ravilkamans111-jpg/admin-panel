"""Write endpoint for editing a Transaction — the first Group B (real
business-logic) write path, per the project's phased write-access plan.

Deliberately its own router/module rather than folded into the generic
`app.api.admin` — a Transaction edit is not a plain column UPDATE (it runs
the balance state machine ported in `app.services.transaction_write_service`
/ `app.services.balance_math`), so pretending it fits the generic
"editable_fields" admin engine would be misleading about what actually
happens on save.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_current_user,
    get_tenant_session,
    get_tenant_write_session,
    require_role,
)
from app.core.exceptions import RecordNotFoundError
from app.core.roles import BrandRole
from app.db.control_plane import get_control_plane_session
from app.models.tenant import Transaction
from app.services import audit_service, transaction_write_service
from app.services.transaction_write_service import EDITABLE_FIELDS

router = APIRouter(prefix="/admin/transactions", tags=["transactions-write"])


class UpdateTransactionRequest(BaseModel):
    status: str | None = None
    amount: str | None = None
    commission: str | None = None
    partner_income: str | None = None
    pure_our_income: str | None = None
    amount_after_commission: str | None = None
    callback_url: str | None = None
    p2p_card: str | None = None
    status_addition_info: str | None = None
    addition_info: str | None = None
    original_tracker_id: str | None = None
    merchant_client_id: str | None = None

    def to_values(self) -> dict[str, object]:
        """Only fields the caller actually set, decimal-cast where needed.

        Amount-like fields arrive as strings (matching how the read side
        already serializes `Decimal` as `str` — see `admin_repository.row_to_dict`)
        so a client can round-trip a GET response straight back into a PATCH
        body without a float ever entering the picture.
        """
        from decimal import Decimal

        decimal_fields = {"amount", "commission", "partner_income", "pure_our_income", "amount_after_commission"}
        raw = self.model_dump(exclude_unset=True)
        return {k: (Decimal(v) if k in decimal_fields and v is not None else v) for k, v in raw.items()}


@router.get("/{pk}/commission-context")
async def commission_context(
    pk: int,
    session: AsyncSession = Depends(get_tenant_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Rates the frontend needs to reproduce the source's live commission
    preview (`static/admin/js/transaction_changes.js`) when the operator
    edits `amount` — see `app.services.transaction_write_service.CommissionContext`.
    `available: false` means source wouldn't have shown a live preview
    either (no matching `MerchantPaymentMethod`) — the frontend should leave
    commission/partner_income/pure_our_income/amount_after_commission as
    plain manually-edited fields in that case, same as source's fallback.
    """
    txn = await session.get(Transaction, pk)
    if txn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Transaction {pk} not found")
    ctx = await transaction_write_service.get_commission_context(session, txn)
    if ctx is None:
        return {"available": False}
    return {
        "available": True,
        "merchant_personal_rate": str(ctx.merchant_personal_rate),
        "partner_rate": str(ctx.partner_rate),
        "direction": ctx.direction,
    }


@router.patch("/{pk}")
async def update_transaction(
    pk: int,
    body: UpdateTransactionRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    values = body.to_values()
    if not values:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No editable fields supplied")
    unknown = [f for f in values if f not in EDITABLE_FIELDS]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not editable on Transaction: {unknown}")

    try:
        before, after = await transaction_write_service.update_transaction(
            tenant_session, brand_id=current_user.brand_id, pk=pk, values=values
        )
    except RecordNotFoundError as exc:
        await tenant_session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        await tenant_session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception:
        await tenant_session.rollback()
        raise
    await tenant_session.commit()

    await audit_service.write_record_change_audit(
        control_plane_session,
        admin_user_id=current_user.admin_user_id,
        brand_id=current_user.brand_id,
        action="update_transaction",
        model_key="transactions",
        record_id=pk,
        before=before,
        after=after,
        ip_address=request.client.host if request.client else None,
    )

    return after
