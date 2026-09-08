"""Write endpoint for editing an AntiFraudBlockedMerchantUsers row.

Own router (not the generic admin engine) for the same reason as
Transaction/Settlement writes: the save runs real diff logic
(`app.services.antifraud_write_service`), not a plain column update.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_write_session, require_role
from app.core.exceptions import RecordNotFoundError
from app.core.roles import BrandRole
from app.db.control_plane import get_control_plane_session
from app.services import antifraud_write_service, audit_service
from app.services.antifraud_write_service import EDITABLE_FIELDS

router = APIRouter(prefix="/admin/antifraud-blocks", tags=["antifraud-write"])


class UpdateAntifraudBlockRequest(BaseModel):
    merchant_id: int | None = None
    user_id: str | None = None
    second_chance: bool | None = None
    permanent_ban: bool | None = None

    def to_values(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


@router.patch("/{pk}")
async def update_antifraud_block(
    pk: int,
    body: UpdateAntifraudBlockRequest,
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
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not editable: {unknown}")

    try:
        before, after = await antifraud_write_service.update_antifraud_block(tenant_session, pk=pk, values=values)
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
        action="update_antifraud_block",
        model_key="antifraud-blocks",
        record_id=pk,
        before=before,
        after=after,
        ip_address=request.client.host if request.client else None,
    )

    return after
