"""Write endpoints for PaymentMethodCascade and PaymentMethodCascadeItem —
the validation state machine ported in `app.services.cascade_write_service`.

Own router (not the generic admin engine) since create/update/delete here
run real cross-record validation, not plain column updates.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_write_session, require_role
from app.core.exceptions import RecordNotFoundError
from app.core.roles import BrandRole
from app.db.control_plane import get_control_plane_session
from app.services import audit_service, cascade_write_service
from app.services.cascade_write_service import CASCADE_EDITABLE_FIELDS, ITEM_EDITABLE_FIELDS

router = APIRouter(tags=["cascade-writes"])


class CreateCascadeRequest(BaseModel):
    name: str
    payment_method_id: int
    description: str | None = None
    is_active: bool = True

    def to_values(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


class UpdateCascadeRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None

    def to_values(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


class CreateCascadeItemRequest(BaseModel):
    payment_method_company_id: int
    priority: int
    is_active: bool = True

    def to_values(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


class UpdateCascadeItemRequest(BaseModel):
    payment_method_company_id: int | None = None
    priority: int | None = None
    is_active: bool | None = None

    def to_values(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


@router.post("/admin/payment-method-cascades", status_code=status.HTTP_201_CREATED)
async def create_cascade(
    body: CreateCascadeRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    values = body.to_values()
    try:
        after = await cascade_write_service.create_cascade(tenant_session, values=values)
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
        action="create_cascade",
        model_key="payment-method-cascades",
        record_id=after["id"],
        before={},
        after=after,
        ip_address=request.client.host if request.client else None,
    )
    return after


@router.patch("/admin/payment-method-cascades/{pk}")
async def update_cascade(
    pk: int,
    body: UpdateCascadeRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    values = body.to_values()
    if not values:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No editable fields supplied")
    unknown = [f for f in values if f not in CASCADE_EDITABLE_FIELDS]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not editable: {unknown}")

    try:
        before, after = await cascade_write_service.update_cascade(tenant_session, pk=pk, values=values)
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
        action="update_cascade",
        model_key="payment-method-cascades",
        record_id=pk,
        before=before,
        after=after,
        ip_address=request.client.host if request.client else None,
    )
    return after


@router.post("/admin/payment-method-cascades/{cascade_id}/items", status_code=status.HTTP_201_CREATED)
async def create_cascade_item(
    cascade_id: int,
    body: CreateCascadeItemRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    values = body.to_values()
    try:
        after = await cascade_write_service.create_cascade_item(tenant_session, cascade_id=cascade_id, values=values)
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
        action="create_cascade_item",
        model_key="payment-method-cascade-items",
        record_id=after["id"],
        before={},
        after=after,
        ip_address=request.client.host if request.client else None,
    )
    return after


@router.patch("/admin/payment-method-cascade-items/{pk}")
async def update_cascade_item(
    pk: int,
    body: UpdateCascadeItemRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    values = body.to_values()
    if not values:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No editable fields supplied")
    unknown = [f for f in values if f not in ITEM_EDITABLE_FIELDS]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not editable: {unknown}")

    try:
        before, after = await cascade_write_service.update_cascade_item(tenant_session, pk=pk, values=values)
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
        action="update_cascade_item",
        model_key="payment-method-cascade-items",
        record_id=pk,
        before=before,
        after=after,
        ip_address=request.client.host if request.client else None,
    )
    return after


@router.delete("/admin/payment-method-cascade-items/{pk}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cascade_item(
    pk: int,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> None:
    try:
        before = await cascade_write_service.delete_cascade_item(tenant_session, pk=pk)
    except RecordNotFoundError as exc:
        await tenant_session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Exception:
        await tenant_session.rollback()
        raise
    await tenant_session.commit()

    await audit_service.write_record_change_audit(
        control_plane_session,
        admin_user_id=current_user.admin_user_id,
        brand_id=current_user.brand_id,
        action="delete_cascade_item",
        model_key="payment-method-cascade-items",
        record_id=pk,
        before=before,
        after={},
        ip_address=request.client.host if request.client else None,
    )
