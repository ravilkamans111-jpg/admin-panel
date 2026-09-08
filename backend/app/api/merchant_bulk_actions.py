"""Write endpoints for the two Merchant bulk actions ported in
`app.services.merchant_bulk_actions_service` — `apply_template_to_merchants`
and `apply_selected_methods_to_merchants`. Own router (not the generic
admin engine): these are multi-record provisioning actions, not a
single-record column update.
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
from app.services import audit_service, merchant_bulk_actions_service

router = APIRouter(prefix="/admin/merchants", tags=["merchant-bulk-actions"])


class ApplyTemplateRequest(BaseModel):
    merchant_ids: list[int]
    template_id: int


class ApplySelectedMethodsRequest(BaseModel):
    merchant_ids: list[int]
    currency_id: int
    direction: str
    method_ids: list[int]
    personal_rate: str
    transaction_min_limit: str | None = None
    transaction_max_limit: str | None = None
    test_mode: bool = True
    only_admin_configure: bool = False


def _result_payload(result) -> dict:
    return {"created_count": result.created_count, "skipped_existing_count": result.skipped_existing_count}


@router.post("/bulk-actions/apply-template")
async def apply_template_to_merchants(
    body: ApplyTemplateRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    try:
        await merchant_bulk_actions_service.validate_merchant_ids(tenant_session, body.merchant_ids)
        result = await merchant_bulk_actions_service.apply_template_to_merchants(
            tenant_session, merchant_ids=body.merchant_ids, template_id=body.template_id
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

    payload = _result_payload(result)
    await audit_service.write_record_change_audit(
        control_plane_session,
        admin_user_id=current_user.admin_user_id,
        brand_id=current_user.brand_id,
        action="apply_template_to_merchants",
        model_key="merchants",
        record_id=",".join(str(i) for i in body.merchant_ids),
        before={},
        after={"template_id": body.template_id, **payload},
        ip_address=request.client.host if request.client else None,
    )
    return payload


@router.post("/bulk-actions/apply-selected-methods")
async def apply_selected_methods_to_merchants(
    body: ApplySelectedMethodsRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    try:
        await merchant_bulk_actions_service.validate_merchant_ids(tenant_session, body.merchant_ids)
        result = await merchant_bulk_actions_service.apply_selected_methods_to_merchants(
            tenant_session,
            merchant_ids=body.merchant_ids,
            currency_id=body.currency_id,
            direction=body.direction,
            method_ids=body.method_ids,
            personal_rate=Decimal(body.personal_rate),
            transaction_min_limit=Decimal(body.transaction_min_limit) if body.transaction_min_limit else None,
            transaction_max_limit=Decimal(body.transaction_max_limit) if body.transaction_max_limit else None,
            test_mode=body.test_mode,
            only_admin_configure=body.only_admin_configure,
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

    payload = _result_payload(result)
    await audit_service.write_record_change_audit(
        control_plane_session,
        admin_user_id=current_user.admin_user_id,
        brand_id=current_user.brand_id,
        action="apply_selected_methods_to_merchants",
        model_key="merchants",
        record_id=",".join(str(i) for i in body.merchant_ids),
        before={},
        after={"currency_id": body.currency_id, "direction": body.direction, "method_ids": body.method_ids, **payload},
        ip_address=request.client.host if request.client else None,
    )
    return payload
