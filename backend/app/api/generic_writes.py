"""Generic create/update/delete endpoints, config-driven off the admin
registry (`app.services.generic_write_service`). Catch-all `{model_key}`
routes — registered in `app.main` AFTER every dedicated write router, so a
model with its own real business logic (Transaction, Settlements, ...)
always hits its own router first; this module only ever actually handles
requests for the "plain" models nothing else claimed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_write_session, require_role
from app.core.exceptions import RecordNotFoundError
from app.core.roles import BrandRole
from app.db.control_plane import get_control_plane_session
from app.registry.admin_models import get_config
from app.services import audit_service, generic_write_service

router = APIRouter(prefix="/admin", tags=["generic-writes"])


def _config_or_404(model_key: str):
    config = get_config(model_key)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown admin model '{model_key}'")
    return config


@router.post("/{model_key}", status_code=status.HTTP_201_CREATED)
async def create_record(
    model_key: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    config = _config_or_404(model_key)
    if not config.creatable:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{model_key}' does not support creation")
    try:
        after = await generic_write_service.create_record(tenant_session, config=config, values=body)
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
        action="create_record",
        model_key=model_key,
        record_id=after.get(config.pk_field, "?"),
        before={},
        after=after,
        ip_address=request.client.host if request.client else None,
    )
    return after


@router.patch("/{model_key}/{pk}")
async def update_record(
    model_key: str,
    pk: int,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> dict:
    config = _config_or_404(model_key)
    if not config.is_writable:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{model_key}' is not editable")
    if not body:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields supplied")
    try:
        before, after = await generic_write_service.update_record(tenant_session, config=config, pk=pk, values=body)
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
        action="update_record",
        model_key=model_key,
        record_id=pk,
        before=before,
        after=after,
        ip_address=request.client.host if request.client else None,
    )
    return after


@router.delete("/{model_key}/{pk}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    model_key: str,
    pk: int,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.OPERATOR)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
    control_plane_session: AsyncSession = Depends(get_control_plane_session),
) -> None:
    config = _config_or_404(model_key)
    if not config.deletable:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{model_key}' is not deletable")
    try:
        before = await generic_write_service.delete_record(tenant_session, config=config, pk=pk)
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
        action="delete_record",
        model_key=model_key,
        record_id=pk,
        before=before,
        after={},
        ip_address=request.client.host if request.client else None,
    )
