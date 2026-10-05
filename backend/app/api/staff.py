"""Управление персоналом (только суперадминистратор)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, require_role
from app.core.exceptions import (
    DuplicateEmailError,
    LastSuperuserError,
    RecordNotFoundError,
    SelfModificationError,
    UnknownBrandError,
)
from app.core.roles import BrandRole
from app.core.security import WeakPasswordError
from app.db.control_plane import get_control_plane_session
from app.repositories import control_plane_repository as repo
from app.services import staff_service

router = APIRouter(prefix="/staff", tags=["staff"])

async def _active_superadmin(
    current_user: CurrentUser = Depends(require_role(BrandRole.SUPERADMIN)),
    session: AsyncSession = Depends(get_control_plane_session),
) -> CurrentUser:
    """The JWT role claim can be up to one access-token lifetime stale, which
    is too loose for account management: re-check the DB on every call."""
    user = await repo.get_admin_user_by_id(session, current_user.admin_user_id)
    if user is None or not user.is_active or not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superuser account required")
    return current_user


SuperAdmin = Depends(_active_superadmin)


class StaffUserOut(BaseModel):
    id: int
    email: str
    full_name: str
    is_active: bool
    is_superuser: bool
    locked: bool
    last_login_at: datetime | None
    brand_access: dict[str, str]


def _out(user: staff_service.StaffUser) -> StaffUserOut:
    return StaffUserOut(**{f: getattr(user, f) for f in StaffUserOut.model_fields})


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, RecordNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, DuplicateEmailError):
        return HTTPException(status.HTTP_409_CONFLICT, "Сотрудник с таким email уже существует")
    if isinstance(exc, WeakPasswordError):
        return HTTPException(422, str(exc))
    if isinstance(exc, UnknownBrandError):
        return HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown brand '{exc}'")
    if isinstance(exc, SelfModificationError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, "Нельзя деактивировать или разжаловать самого себя")
    if isinstance(exc, LastSuperuserError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, "Должен остаться хотя бы один активный суперпользователь")
    raise exc


_HANDLED = (
    RecordNotFoundError, DuplicateEmailError, WeakPasswordError, UnknownBrandError,
    SelfModificationError, LastSuperuserError,
)


@router.get("/users", response_model=list[StaffUserOut])
async def list_users(
    current_user: CurrentUser = SuperAdmin,
    session: AsyncSession = Depends(get_control_plane_session),
) -> list[StaffUserOut]:
    return [_out(u) for u in await staff_service.list_staff(session)]


class CreateStaffRequest(BaseModel):
    email: EmailStr
    full_name: str = ""
    password: str
    is_superuser: bool = False
    brand_access: dict[str, BrandRole] = {}


@router.post("/users", response_model=StaffUserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateStaffRequest,
    request: Request,
    current_user: CurrentUser = SuperAdmin,
    session: AsyncSession = Depends(get_control_plane_session),
) -> StaffUserOut:
    try:
        user = await staff_service.create_staff(
            session, actor_id=current_user.admin_user_id, email=body.email, full_name=body.full_name,
            password=body.password, is_superuser=body.is_superuser, brand_access=body.brand_access,
            ip_address=_ip(request),
        )
    except _HANDLED as exc:
        raise _translate(exc) from exc
    return _out(user)


class UpdateStaffRequest(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None


@router.patch("/users/{user_id}", response_model=StaffUserOut)
async def update_user(
    user_id: int,
    body: UpdateStaffRequest,
    request: Request,
    current_user: CurrentUser = SuperAdmin,
    session: AsyncSession = Depends(get_control_plane_session),
) -> StaffUserOut:
    try:
        user = await staff_service.update_staff(
            session, actor_id=current_user.admin_user_id, user_id=user_id, full_name=body.full_name,
            is_active=body.is_active, is_superuser=body.is_superuser, ip_address=_ip(request),
        )
    except _HANDLED as exc:
        raise _translate(exc) from exc
    return _out(user)


class ResetPasswordRequest(BaseModel):
    new_password: str


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: int,
    body: ResetPasswordRequest,
    request: Request,
    current_user: CurrentUser = SuperAdmin,
    session: AsyncSession = Depends(get_control_plane_session),
) -> None:
    try:
        await staff_service.reset_password(
            session, actor_id=current_user.admin_user_id, user_id=user_id,
            new_password=body.new_password, ip_address=_ip(request),
        )
    except _HANDLED as exc:
        raise _translate(exc) from exc


@router.post("/users/{user_id}/unlock", status_code=status.HTTP_204_NO_CONTENT)
async def unlock_user(
    user_id: int,
    request: Request,
    current_user: CurrentUser = SuperAdmin,
    session: AsyncSession = Depends(get_control_plane_session),
) -> None:
    try:
        await staff_service.unlock(session, actor_id=current_user.admin_user_id, user_id=user_id, ip_address=_ip(request))
    except _HANDLED as exc:
        raise _translate(exc) from exc


class BrandAccessRequest(BaseModel):
    role: BrandRole | None  # null = revoke


@router.put("/users/{user_id}/brand-access/{brand_id}", response_model=StaffUserOut)
async def set_brand_access(
    user_id: int,
    brand_id: str,
    body: BrandAccessRequest,
    request: Request,
    current_user: CurrentUser = SuperAdmin,
    session: AsyncSession = Depends(get_control_plane_session),
) -> StaffUserOut:
    try:
        user = await staff_service.set_brand_access(
            session, actor_id=current_user.admin_user_id, user_id=user_id, brand_id=brand_id,
            role=body.role, ip_address=_ip(request),
        )
    except _HANDLED as exc:
        raise _translate(exc) from exc
    return _out(user)
