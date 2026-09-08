"""Write endpoints for PaymentMethodCompany and MerchantPaymentMethod.

Own router (not the generic admin engine) for the same reason as every
other Group B model: the save runs real side effects — Redis cache
invalidation — not a plain column update. See
`app.services.payment_method_write_service` / `app.services.cache_invalidation`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_write_session, require_role
from app.core.exceptions import RecordNotFoundError
from app.core.roles import BrandRole
from app.db.control_plane import get_control_plane_session
from app.services import audit_service, payment_method_write_service
from app.services.payment_method_write_service import MPM_EDITABLE_FIELDS, PMC_EDITABLE_FIELDS

router = APIRouter(tags=["payment-method-writes"])


class UpdatePaymentMethodCompanyRequest(BaseModel):
    is_active: bool | None = None
    priority: int | None = None
    partner_rate: str | None = None
    additional_commission: str | None = None
    settlement_commission: str | None = None
    daily_amount_limit: int | None = None
    daily_count_limit: int | None = None
    transaction_min_limit: str | None = None
    transaction_max_limit: str | None = None

    def to_values(self) -> dict[str, object]:
        from decimal import Decimal

        decimal_fields = {
            "partner_rate",
            "additional_commission",
            "settlement_commission",
            "transaction_min_limit",
            "transaction_max_limit",
        }
        raw = self.model_dump(exclude_unset=True)
        return {k: (Decimal(v) if k in decimal_fields and v is not None else v) for k, v in raw.items()}


class UpdateMerchantPaymentMethodRequest(BaseModel):
    personal_rate: str | None = None
    test_mode: bool | None = None
    additional_commission: str | None = None
    block: bool | None = None
    transaction_min_limit: str | None = None
    transaction_max_limit: str | None = None
    no_callback: bool | None = None
    only_admin_configure: bool | None = None
    cascade_id: int | None = None

    def to_values(self) -> dict[str, object]:
        from decimal import Decimal

        decimal_fields = {"personal_rate", "additional_commission", "transaction_min_limit", "transaction_max_limit"}
        raw = self.model_dump(exclude_unset=True)
        return {k: (Decimal(v) if k in decimal_fields and v is not None else v) for k, v in raw.items()}


@router.patch("/admin/payment-method-companies/{pk}")
async def update_payment_method_company(
    pk: int,
    body: UpdatePaymentMethodCompanyRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    values = body.to_values()
    if not values:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No editable fields supplied")
    unknown = [f for f in values if f not in PMC_EDITABLE_FIELDS]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not editable: {unknown}")

    try:
        before, after = await payment_method_write_service.update_payment_method_company(
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
        action="update_payment_method_company",
        model_key="payment-method-companies",
        record_id=pk,
        before=before,
        after=after,
        ip_address=request.client.host if request.client else None,
    )

    return after


@router.patch("/admin/merchant-payment-methods/{pk}")
async def update_merchant_payment_method(
    pk: int,
    body: UpdateMerchantPaymentMethodRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    values = body.to_values()
    if not values:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No editable fields supplied")
    unknown = [f for f in values if f not in MPM_EDITABLE_FIELDS]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not editable: {unknown}")

    try:
        before, after = await payment_method_write_service.update_merchant_payment_method(
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
        action="update_merchant_payment_method",
        model_key="merchant-payment-methods",
        record_id=pk,
        before=before,
        after=after,
        ip_address=request.client.host if request.client else None,
    )

    return after
