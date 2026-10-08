"""HTTP handler for the landing-dashboard summary. Thin: delegates entirely
to `app.services.dashboard_service`."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_tenant_session
from app.core.ttl_cache import TTLCache
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# COUNT(*)/GROUP BY over the whole transactions table on every page open is the one
# expensive thing a landing page does — a minute of staleness is fine for totals.
_summary_cache = TTLCache(ttl_seconds=60)


@router.get("/summary")
async def summary(
    session: AsyncSession = Depends(get_tenant_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    return await _summary_cache.get_or_compute(
        current_user.brand_id, lambda: dashboard_service.get_summary(session, brand_id=current_user.brand_id)
    )
