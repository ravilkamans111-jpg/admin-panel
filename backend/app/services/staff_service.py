"""Управление учётными записями персонала (control-plane БД).

Только суперпользователь. Каждое действие пишется в `audit_log`. Защиты:
нельзя деактивировать/разжаловать себя и нельзя оставить систему без
активного суперпользователя; смена пароля/деактивация отзывает все сессии
пользователя (bump `token_version`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.brands import KNOWN_BRANDS
from app.core.exceptions import (
    DuplicateEmailError,
    LastSuperuserError,
    RecordNotFoundError,
    SelfModificationError,
    UnknownBrandError,
)
from app.core.roles import BrandRole
from app.core.security import hash_password, validate_password_strength
from app.core.settings_env import env_settings
from app.models.control_plane import AdminUser, BrandAccess
from app.repositories import control_plane_repository as repo


@dataclass(frozen=True, slots=True)
class StaffUser:
    id: int
    email: str
    full_name: str
    is_active: bool
    is_superuser: bool
    locked: bool
    last_login_at: datetime | None
    brand_access: dict[str, str] = field(default_factory=dict)


def _to_staff(user: AdminUser, access: list[BrandAccess]) -> StaffUser:
    locked_until = user.locked_until
    if locked_until is not None and locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=UTC)
    return StaffUser(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        locked=bool(locked_until and locked_until > datetime.now(UTC)),
        last_login_at=user.last_login_at,
        brand_access={a.brand_id: a.role.value for a in access if a.admin_user_id == user.id},
    )


def _check_brand(brand_id: str) -> None:
    if brand_id not in KNOWN_BRANDS:
        raise UnknownBrandError(brand_id)


async def _get(session: AsyncSession, user_id: int) -> AdminUser:
    user = await repo.get_admin_user_by_id(session, user_id)
    if user is None:
        raise RecordNotFoundError(f"Staff user {user_id} not found")
    return user


async def list_staff(session: AsyncSession) -> list[StaffUser]:
    users = await repo.list_admin_users(session)
    access = await repo.list_all_brand_access(session)
    return [_to_staff(u, access) for u in users]


async def get_staff(session: AsyncSession, user_id: int) -> StaffUser:
    user = await _get(session, user_id)
    return _to_staff(user, await repo.get_brand_access_list(session, user_id))


async def create_staff(
    session: AsyncSession,
    *,
    actor_id: int,
    email: str,
    full_name: str,
    password: str,
    is_superuser: bool,
    brand_access: dict[str, BrandRole],
    ip_address: str | None,
) -> StaffUser:
    email = email.strip().lower()
    validate_password_strength(password, min_length=env_settings.min_password_length, email=email)
    for brand_id in brand_access:
        _check_brand(brand_id)
    if await repo.get_admin_user_by_email(session, email) is not None:
        raise DuplicateEmailError(email)

    user = AdminUser(
        email=email,
        full_name=full_name.strip(),
        password_hash=hash_password(password),
        is_active=True,
        is_superuser=is_superuser,
    )
    repo.add_admin_user(session, user)
    await session.flush()
    for brand_id, role in brand_access.items():
        repo.add_brand_access(session, BrandAccess(admin_user_id=user.id, brand_id=brand_id, role=role))
    await repo.write_audit_log_no_commit(
        session, admin_user_id=actor_id, brand_id=None, action="staff_create",
        detail={"target_id": user.id, "email": email, "is_superuser": is_superuser,
                "brand_access": {b: r.value for b, r in brand_access.items()}},
        ip_address=ip_address,
    )
    await session.commit()
    return await get_staff(session, user.id)


async def update_staff(
    session: AsyncSession,
    *,
    actor_id: int,
    user_id: int,
    full_name: str | None,
    is_active: bool | None,
    is_superuser: bool | None,
    ip_address: str | None,
) -> StaffUser:
    user = await _get(session, user_id)
    changes: dict[str, object] = {}

    if user_id == actor_id and (is_active is False or is_superuser is False):
        raise SelfModificationError

    if full_name is not None and full_name.strip() != user.full_name:
        user.full_name = full_name.strip()
        changes["full_name"] = user.full_name
    if is_active is not None and is_active != user.is_active:
        user.is_active = is_active
        if not is_active:
            user.token_version += 1
        changes["is_active"] = is_active
    if is_superuser is not None and is_superuser != user.is_superuser:
        user.is_superuser = is_superuser
        changes["is_superuser"] = is_superuser

    await session.flush()
    if await repo.count_active_superusers(session) == 0:
        await session.rollback()
        raise LastSuperuserError

    if changes:
        await repo.write_audit_log_no_commit(
            session, admin_user_id=actor_id, brand_id=None, action="staff_update",
            detail={"target_id": user_id, **changes}, ip_address=ip_address,
        )
    await session.commit()
    return await get_staff(session, user_id)


async def reset_password(
    session: AsyncSession, *, actor_id: int, user_id: int, new_password: str, ip_address: str | None
) -> None:
    user = await _get(session, user_id)
    validate_password_strength(new_password, min_length=env_settings.min_password_length, email=user.email)
    user.password_hash = hash_password(new_password)
    user.password_changed_at = datetime.now(UTC)
    user.token_version += 1
    user.failed_login_attempts = 0
    user.locked_until = None
    await repo.write_audit_log_no_commit(
        session, admin_user_id=actor_id, brand_id=None, action="staff_reset_password",
        detail={"target_id": user_id}, ip_address=ip_address,
    )
    await session.commit()


async def unlock(session: AsyncSession, *, actor_id: int, user_id: int, ip_address: str | None) -> None:
    user = await _get(session, user_id)
    user.failed_login_attempts = 0
    user.locked_until = None
    await repo.write_audit_log_no_commit(
        session, admin_user_id=actor_id, brand_id=None, action="staff_unlock",
        detail={"target_id": user_id}, ip_address=ip_address,
    )
    await session.commit()


async def set_brand_access(
    session: AsyncSession,
    *,
    actor_id: int,
    user_id: int,
    brand_id: str,
    role: BrandRole | None,
    ip_address: str | None,
) -> StaffUser:
    """`role=None` revokes access to `brand_id`."""
    _check_brand(brand_id)
    await _get(session, user_id)
    existing = await repo.get_brand_access(session, user_id, brand_id)
    if role is None:
        if existing is not None:
            await repo.delete_brand_access(session, user_id, brand_id)
    elif existing is None:
        repo.add_brand_access(session, BrandAccess(admin_user_id=user_id, brand_id=brand_id, role=role))
    else:
        existing.role = role
    await repo.write_audit_log_no_commit(
        session, admin_user_id=actor_id, brand_id=brand_id, action="staff_set_brand_access",
        detail={"target_id": user_id, "role": role.value if role else None}, ip_address=ip_address,
    )
    await session.commit()
    return await get_staff(session, user_id)
