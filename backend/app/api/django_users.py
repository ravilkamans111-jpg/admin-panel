"""Creating a user, like the monolith's Django admin "Add user" form.

Django's own form takes username + password + confirmation, creates an active
non-staff account, and only then lets you tick staff/superuser and fill in names
(on the change page — here: PATCH /admin/users/{id}, generic engine). Passwords
are stored the way `django.contrib.auth` stores them (PBKDF2-SHA256), so the
monolith can log that user in straight away. Superadmin only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_write_session, require_role
from app.core.roles import BrandRole
from app.services import audit_service, django_user_service

router = APIRouter(prefix="/admin/users", tags=["users-write"])


class CreateUserRequest(BaseModel):
    username: str
    password1: str
    password2: str


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_role(BrandRole.SUPERADMIN)),
    tenant_session: AsyncSession = Depends(get_tenant_write_session),
) -> dict:
    try:
        created = await django_user_service.create_user(
            tenant_session, username=body.username, password1=body.password1, password2=body.password2
        )
    except django_user_service.UserValidationError as exc:
        await tenant_session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.errors) from exc
    except Exception:
        await tenant_session.rollback()
        raise
    await tenant_session.commit()
    await audit_service.write_record_change_audit(
        admin_user_id=current_user.admin_user_id, brand_id=current_user.brand_id, action="create_user",
        model_key="users", record_id=created["id"], before={}, after=created,
        ip_address=request.client.host if request.client else None,
    )
    return created
