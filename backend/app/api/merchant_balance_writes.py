"""Write endpoint for the MerchantBalance raw-SQL recompute, ported in
`app.services.merchant_balance_service`. Own router (not the generic admin
engine): a bulk raw-SQL UPDATE across every merchant/currency pair, not a
single-record column update.

Source gates this on Django's `request.user.is_superuser` — the closest
equivalent in this project's RBAC is `BrandRole.SUPERADMIN`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_write_session, require_role
from app.core.roles import BrandRole
from app.db.control_plane import get_control_plane_session
from app.services import audit_service, merchant_balance_service

router = APIRouter(prefix="/admin/merchant-balances", tags=["merchant-balance-writes"])


@router.post("/refresh")
async def refresh_merchant_balances(
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.SUPERADMIN)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    try:
        updated_count = await merchant_balance_service.refresh_all_merchant_balances(tenant_session)
    except Exception:
        await tenant_session.rollback()
        raise
    await tenant_session.commit()

    await audit_service.write_record_change_audit(
        control_plane_session,
        admin_user_id=current_user.admin_user_id,
        brand_id=current_user.brand_id,
        action="refresh_merchant_balances",
        model_key="merchant-balances",
        record_id="bulk",
        before={},
        after={"updated_count": updated_count},
        ip_address=request.client.host if request.client else None,
    )
    return {"updated_count": updated_count}
