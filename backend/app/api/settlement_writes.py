"""Write endpoints for creating/editing a Settlement.

Own router, same reasoning as `app.api.transaction_writes`: a Settlement
save runs real business logic (creates/updates a linked Transaction and
mutates balances via the same balance-math the Transaction write path
uses) — not a plain column write, so it doesn't belong on the generic
admin engine.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_write_session, require_role
from app.core.exceptions import RecordNotFoundError
from app.core.roles import BrandRole
from app.db.control_plane import get_control_plane_session
from app.services import audit_service, settlement_write_service
from app.services.settlement_write_service import CREATE_ONLY_FIELDS, EDITABLE_FIELDS

router = APIRouter(prefix="/admin/settlements", tags=["settlements-write"])

_DECIMAL_FIELDS = {
    "amount", "commission", "our_funds", "clients_funds", "conversion_rate",
    "amount_in_usdt", "final_amount", "final_amount_in_usdt",
}


class SettlementWriteRequest(BaseModel):
    settl_type: str | None = None
    balance_merchant_id: int | None = None
    balance_partner_id: int | None = None
    status: str | None = None
    amount: str | None = None
    commission: str | None = None
    our_funds: str | None = None
    clients_funds: str | None = None
    conversion_rate: str | None = None
    amount_in_usdt: str | None = None
    final_amount: str | None = None
    final_amount_in_usdt: str | None = None
    wallet: str | None = None
    tracker_link: str | None = None
    tg_id: str | None = None

    def to_values(self) -> dict[str, object]:
        raw = self.model_dump(exclude_unset=True)
        return {k: (Decimal(v) if k in _DECIMAL_FIELDS and v is not None else v) for k, v in raw.items()}


def _response(result) -> dict:
    return {"settlement": result.settlement_after, "transaction": result.transaction_after}


@router.post("")
async def create_settlement(
    body: SettlementWriteRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    values = body.to_values()
    allowed = set(EDITABLE_FIELDS) | set(CREATE_ONLY_FIELDS)
    unknown = [f for f in values if f not in allowed]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not a valid field on Settlement: {unknown}")

    try:
        result = await settlement_write_service.create_or_update_settlement(
            tenant_session, brand_id=current_user.brand_id, pk=None, values=values
        )
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
        action="create_settlement",
        model_key="settlements",
        record_id=result.settlement_after["id"],
        before=None,
        after=result.settlement_after,
        extra={"linked_transaction": result.transaction_after},
        ip_address=request.client.host if request.client else None,
    )

    return _response(result)


@router.patch("/{pk}")
async def update_settlement(
    pk: int,
    body: SettlementWriteRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    values = body.to_values()
    unknown = [f for f in values if f not in EDITABLE_FIELDS]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not editable on Settlement: {unknown}")
    if not values:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No editable fields supplied")

    try:
        result = await settlement_write_service.create_or_update_settlement(
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
        action="update_settlement",
        model_key="settlements",
        record_id=pk,
        before=result.settlement_before,
        after=result.settlement_after,
        extra={"linked_transaction": result.transaction_after},
        ip_address=request.client.host if request.client else None,
    )

    return _response(result)
