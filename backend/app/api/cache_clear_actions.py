"""Endpoints for the three reachable cache-clear admin actions ported in
`app.services.cache_clear_actions_service`. These don't mutate any Postgres
row — only the Redis method-lookup cache — so they use the read-only tenant
session (`get_tenant_session`) for the lookup half, and RBAC still gates
write access via `require_role(OPERATOR)` since this is an operator-facing
admin action, not a passive read.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_session, require_role
from app.core.exceptions import RecordNotFoundError
from app.core.roles import BrandRole
from app.db.control_plane import get_control_plane_session
from app.services import audit_service, cache_clear_actions_service

router = APIRouter(prefix="/admin/payment-methods-cache", tags=["cache-clear-actions"])


class ClearByCurrencyRequest(BaseModel):
    payment_method_company_ids: list[int]


class ClearByMerchantRequest(BaseModel):
    merchant_payment_method_ids: list[int]


@router.post("/clear-by-currency")
async def clear_cache_by_currency(
    body: ClearByCurrencyRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    try:
        currencies = await cache_clear_actions_service.clear_cache_by_currency(
            tenant_session, brand_id=current_user.brand_id, payment_method_company_ids=body.payment_method_company_ids
        )
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await audit_service.write_record_change_audit(
        control_plane_session,
        admin_user_id=current_user.admin_user_id,
        brand_id=current_user.brand_id,
        action="clear_cache_by_currency",
        model_key="payment-method-companies",
        record_id=",".join(str(i) for i in body.payment_method_company_ids),
        before={},
        after={"currencies": currencies},
        ip_address=request.client.host if request.client else None,
    )
    return {"currencies": currencies}


@router.post("/clear-by-merchant")
async def clear_cache_by_merchant(
    body: ClearByMerchantRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    try:
        merchants = await cache_clear_actions_service.clear_cache_by_merchant(
            tenant_session, brand_id=current_user.brand_id, merchant_payment_method_ids=body.merchant_payment_method_ids
        )
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await audit_service.write_record_change_audit(
        control_plane_session,
        admin_user_id=current_user.admin_user_id,
        brand_id=current_user.brand_id,
        action="clear_cache_by_merchant",
        model_key="merchant-payment-methods",
        record_id=",".join(str(i) for i in body.merchant_payment_method_ids),
        before={},
        after={"merchants": merchants},
        ip_address=request.client.host if request.client else None,
    )
    return {"merchants": merchants}


@router.post("/clear-all")
async def clear_all_payment_methods_cache(
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    await cache_clear_actions_service.clear_all_payment_methods_cache(current_user.brand_id)

    await audit_service.write_record_change_audit(
        control_plane_session,
        admin_user_id=current_user.admin_user_id,
        brand_id=current_user.brand_id,
        action="clear_all_payment_methods_cache",
        model_key="payment-method-companies",
        record_id="all",
        before={},
        after={},
        ip_address=request.client.host if request.client else None,
    )
    return {"status": "ok"}
