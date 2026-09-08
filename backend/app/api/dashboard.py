"""HTTP handler for the landing-dashboard summary. Thin: delegates entirely
to `app.services.dashboard_service`."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_tenant_session
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
async def summary(
    session: AsyncSession = Depends(get_tenant_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    return await dashboard_service.get_summary(session, brand_id=current_user.brand_id)
