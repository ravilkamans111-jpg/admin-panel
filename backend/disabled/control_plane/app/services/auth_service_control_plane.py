"""Бизнес-логика двухшаговой аутентификации.

Учётные записи персонала живут только в control-plane БД (`admin_user`,
`brand_access`); никаких встроенных учёток нет. Слой сервисов не знает о
FastAPI: он поднимает исключения из `app.core.exceptions`, а перевод их в
HTTP-коды — работа обработчика (`app.api.auth`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.brands import KNOWN_BRANDS
from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    BrandAccessDeniedError,
    InvalidCredentialsError,
    InvalidTokenError,
    RecordNotFoundError,
    UnknownBrandError,
)
from app.core.roles import BrandRole
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    TokenError,
    TokenScope,
    create_access_token,
    create_pre_auth_token,
    create_refresh_token,
    decode_token,
    hash_password,
    password_needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.core.settings_env import env_settings
from app.models.control_plane import AdminUser
from app.repositories import control_plane_repository as repo


@dataclass(frozen=True, slots=True)
class BrandOption:
    brand_id: str
    display_name: str
    role: str


@dataclass(frozen=True, slots=True)
class LoginResult:
    pre_auth_token: str
    available_brands: list[BrandOption]


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    brand_id: str
    role: str


@dataclass(frozen=True, slots=True)
class CurrentAdminUser:
    admin_user_id: int
    email: str
    full_name: str
    is_superuser: bool


def _now() -> datetime:
    return datetime.now(UTC)


def _is_locked(user: AdminUser) -> bool:
    if user.locked_until is None:
        return False
    locked_until = user.locked_until if user.locked_until.tzinfo else user.locked_until.replace(tzinfo=UTC)
    return locked_until > _now()


async def _resolve_available_brands(session: AsyncSession, user: AdminUser) -> list[BrandOption]:
    if user.is_superuser:
        return [
            BrandOption(brand_id=b.brand_id, display_name=b.display_name, role=BrandRole.SUPERADMIN.value)
            for b in KNOWN_BRANDS.values()
        ]
    access_rows = await repo.get_brand_access_list(session, user.id)
    return [
        BrandOption(
            brand_id=row.brand_id,
            display_name=KNOWN_BRANDS[row.brand_id].display_name if row.brand_id in KNOWN_BRANDS else row.brand_id,
            role=row.role.value,
        )
        for row in access_rows
        if row.brand_id in KNOWN_BRANDS
    ]


async def _resolve_role(session: AsyncSession, user: AdminUser, brand_id: str) -> str:
    if user.is_superuser:
        return BrandRole.SUPERADMIN.value
    access = await repo.get_brand_access(session, user.id, brand_id)
    if access is None:
        raise BrandAccessDeniedError(brand_id)
    return access.role.value


async def login(session: AsyncSession, *, email: str, password: str, ip_address: str | None) -> LoginResult:
    user = await repo.get_admin_user_by_email(session, email)

    if user is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        await repo.write_audit_log(
            session, admin_user_id=None, brand_id=None, action="login_failed",
            detail={"email": email}, ip_address=ip_address,
        )
        raise InvalidCredentialsError

    if _is_locked(user):
        await repo.write_audit_log(
            session, admin_user_id=user.id, brand_id=None, action="login_locked", ip_address=ip_address
        )
        raise AccountLockedError

    password_ok = verify_password(password, user.password_hash)
    if not password_ok or not user.is_active:
        if user.is_active:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= env_settings.login_max_failed_attempts:
                user.locked_until = _now() + timedelta(minutes=env_settings.login_lockout_minutes)
                user.failed_login_attempts = 0
        await session.commit()
        await repo.write_audit_log(
            session, admin_user_id=user.id, brand_id=None, action="login_failed",
            detail={"email": email}, ip_address=ip_address,
        )
        raise InvalidCredentialsError

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = _now()
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    await session.commit()

    available_brands = await _resolve_available_brands(session, user)
    await repo.write_audit_log(session, admin_user_id=user.id, brand_id=None, action="login", ip_address=ip_address)
    return LoginResult(pre_auth_token=create_pre_auth_token(user.id), available_brands=available_brands)


def _issue_tokens(user: AdminUser, brand_id: str, role: str) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user.id, brand_id, role),
        refresh_token=create_refresh_token(user.id, brand_id, role, user.token_version),
        brand_id=brand_id,
        role=role,
    )


async def select_brand(
    session: AsyncSession, *, admin_user_id: int, brand_id: str, ip_address: str | None
) -> TokenPair:
    if brand_id not in KNOWN_BRANDS:
        raise UnknownBrandError(brand_id)

    user = await repo.get_admin_user_by_id(session, admin_user_id)
    if user is None or not user.is_active:
        raise AccountInactiveError

    try:
        role = await _resolve_role(session, user, brand_id)
    except BrandAccessDeniedError:
        await repo.write_audit_log(
            session, admin_user_id=admin_user_id, brand_id=brand_id, action="access_denied", ip_address=ip_address
        )
        raise

    await repo.write_audit_log(
        session, admin_user_id=admin_user_id, brand_id=brand_id, action="select_brand", ip_address=ip_address
    )
    return _issue_tokens(user, brand_id, role)


async def refresh_tokens(session: AsyncSession, *, refresh_token: str) -> TokenPair:
    try:
        claims = decode_token(refresh_token, TokenScope.REFRESH)
    except TokenError as exc:
        raise InvalidTokenError(str(exc)) from exc

    if not claims.brand_id:
        raise AccountInactiveError

    user = await repo.get_admin_user_by_id(session, int(claims.sub))
    if user is None or not user.is_active:
        raise AccountInactiveError
    if claims.ver != user.token_version:
        raise InvalidTokenError("Session revoked")

    # Brand access is re-checked on every refresh so a revoked grant takes
    # effect without waiting for the refresh token to expire.
    role = await _resolve_role(session, user, claims.brand_id)
    return _issue_tokens(user, claims.brand_id, role)


async def get_current_admin_user(session: AsyncSession, *, admin_user_id: int) -> CurrentAdminUser:
    user = await repo.get_admin_user_by_id(session, admin_user_id)
    if user is None:
        raise RecordNotFoundError("Admin user no longer exists")
    return CurrentAdminUser(
        admin_user_id=user.id, email=user.email, full_name=user.full_name, is_superuser=user.is_superuser
    )


async def change_password(
    session: AsyncSession,
    *,
    admin_user_id: int,
    brand_id: str,
    current_password: str,
    new_password: str,
    ip_address: str | None,
) -> TokenPair:
    """Verifies the current password, sets the new one and revokes every other
    session (token_version bump). Returns a fresh token pair for the caller so
    their own session survives. Raises WeakPasswordError (core.security) if the
    new password fails policy."""
    user = await repo.get_admin_user_by_id(session, admin_user_id)
    if user is None or not user.is_active:
        raise AccountInactiveError
    if not verify_password(current_password, user.password_hash):
        await repo.write_audit_log(
            session, admin_user_id=user.id, brand_id=brand_id, action="change_password_failed", ip_address=ip_address
        )
        raise InvalidCredentialsError
    validate_password_strength(new_password, min_length=env_settings.min_password_length, email=user.email)

    user.password_hash = hash_password(new_password)
    user.password_changed_at = _now()
    user.token_version += 1
    await session.commit()
    await repo.write_audit_log(
        session, admin_user_id=user.id, brand_id=brand_id, action="change_password", ip_address=ip_address
    )
    role = await _resolve_role(session, user, brand_id)
    return _issue_tokens(user, brand_id, role)
