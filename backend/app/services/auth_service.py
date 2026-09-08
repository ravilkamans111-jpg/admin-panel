"""Бизнес-логика двухшаговой аутентификации.

Слой сервисов не знает о FastAPI: он поднимает исключения из
`app.core.exceptions`, а перевод их в HTTP-коды — работа обработчика
(`app.api.auth`). Использует только `app.repositories.control_plane_repository`
для доступа к данным и `app.core.security`/`app.core.brands` для правил.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.brands import KNOWN_BRANDS
from app.core.exceptions import (
    AccountInactiveError,
    BrandAccessDeniedError,
    InvalidCredentialsError,
    InvalidTokenError,
    RecordNotFoundError,
    UnknownBrandError,
)
from app.core.roles import BrandRole
from app.core.security import (
    TokenError,
    TokenScope,
    create_access_token,
    create_pre_auth_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.core.settings_env import env_settings
from app.repositories import control_plane_repository as repo

# Sentinel `admin_user_id` for the hardcoded superuser (see `settings_env.
# hardcoded_superuser_email`/`hardcoded_superuser_password`) — never a real
# `admin_user` row, by design (see `app.models.control_plane.AuditLog` and
# migration 0002 for why `audit_log.admin_user_id` has no FK). Negative so
# it can never collide with a real Postgres serial-generated admin_user.id.
HARDCODED_SUPERUSER_ID = -1


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


async def _resolve_available_brands(session: AsyncSession, admin_user_id: int, is_superuser: bool) -> list[BrandOption]:
    if is_superuser:
        return [
            BrandOption(brand_id=b.brand_id, display_name=b.display_name, role=BrandRole.SUPERADMIN.value)
            for b in KNOWN_BRANDS.values()
        ]
    access_rows = await repo.get_brand_access_list(session, admin_user_id)
    return [
        BrandOption(
            brand_id=row.brand_id,
            display_name=KNOWN_BRANDS[row.brand_id].display_name if row.brand_id in KNOWN_BRANDS else row.brand_id,
            role=row.role.value,
        )
        for row in access_rows
    ]


def _matches_hardcoded_superuser(email: str, password: str) -> bool:
    """Constant-time check against the single hardcoded superuser identity
    (see `HARDCODED_SUPERUSER_ID`). Email comparison doesn't need to be
    constant-time (it's not a secret); the password comparison does."""
    return email.strip().lower() == env_settings.hardcoded_superuser_email.strip().lower() and hmac.compare_digest(
        password, env_settings.hardcoded_superuser_password
    )


async def login(
    session: AsyncSession, *, email: str, password: str, ip_address: str | None
) -> LoginResult:
    if _matches_hardcoded_superuser(email, password):
        available_brands = await _resolve_available_brands(session, HARDCODED_SUPERUSER_ID, is_superuser=True)
        await repo.write_audit_log(
            session,
            admin_user_id=HARDCODED_SUPERUSER_ID,
            brand_id=None,
            action="login",
            detail={"email": email},
            ip_address=ip_address,
        )
        return LoginResult(
            pre_auth_token=create_pre_auth_token(HARDCODED_SUPERUSER_ID), available_brands=available_brands
        )

    user = await repo.get_admin_user_by_email(session, email)
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        await repo.write_audit_log(
            session,
            admin_user_id=user.id if user else None,
            brand_id=None,
            action="login_failed",
            detail={"email": email},
            ip_address=ip_address,
        )
        raise InvalidCredentialsError

    available_brands = await _resolve_available_brands(session, user.id, user.is_superuser)

    await repo.write_audit_log(
        session, admin_user_id=user.id, brand_id=None, action="login", ip_address=ip_address
    )

    return LoginResult(pre_auth_token=create_pre_auth_token(user.id), available_brands=available_brands)


async def select_brand(
    session: AsyncSession, *, admin_user_id: int, brand_id: str, ip_address: str | None
) -> TokenPair:
    if brand_id not in KNOWN_BRANDS:
        raise UnknownBrandError(brand_id)

    if admin_user_id == HARDCODED_SUPERUSER_ID:
        role = BrandRole.SUPERADMIN.value
    else:
        user = await repo.get_admin_user_by_id(session, admin_user_id)
        if user is None or not user.is_active:
            raise AccountInactiveError

        if user.is_superuser:
            role = BrandRole.SUPERADMIN.value
        else:
            access = await repo.get_brand_access(session, admin_user_id, brand_id)
            if access is None:
                await repo.write_audit_log(
                    session, admin_user_id=admin_user_id, brand_id=brand_id, action="access_denied", ip_address=ip_address
                )
                raise BrandAccessDeniedError(brand_id)
            role = access.role.value

    await repo.write_audit_log(
        session, admin_user_id=admin_user_id, brand_id=brand_id, action="select_brand", ip_address=ip_address
    )

    return TokenPair(
        access_token=create_access_token(admin_user_id, brand_id, role),
        refresh_token=create_refresh_token(admin_user_id, brand_id, role),
        brand_id=brand_id,
        role=role,
    )


async def refresh_tokens(session: AsyncSession, *, refresh_token: str) -> TokenPair:
    try:
        claims = decode_token(refresh_token, TokenScope.REFRESH)
    except TokenError as exc:
        raise InvalidTokenError(str(exc)) from exc

    admin_user_id = int(claims.sub)
    if not claims.brand_id:
        raise AccountInactiveError

    # Re-check brand access on every refresh — a revoked BrandAccess grant
    # must take effect without waiting for the access token to expire.
    if admin_user_id == HARDCODED_SUPERUSER_ID:
        role = BrandRole.SUPERADMIN.value
    else:
        user = await repo.get_admin_user_by_id(session, admin_user_id)
        if user is None or not user.is_active:
            raise AccountInactiveError

        if user.is_superuser:
            role = BrandRole.SUPERADMIN.value
        else:
            access = await repo.get_brand_access(session, admin_user_id, claims.brand_id)
            if access is None:
                raise BrandAccessDeniedError(claims.brand_id)
            role = access.role.value

    return TokenPair(
        access_token=create_access_token(admin_user_id, claims.brand_id, role),
        refresh_token=create_refresh_token(admin_user_id, claims.brand_id, role),
        brand_id=claims.brand_id,
        role=role,
    )


async def get_current_admin_user(session: AsyncSession, *, admin_user_id: int) -> CurrentAdminUser:
    if admin_user_id == HARDCODED_SUPERUSER_ID:
        return CurrentAdminUser(
            admin_user_id=HARDCODED_SUPERUSER_ID,
            email=env_settings.hardcoded_superuser_email,
            full_name="Суперадминистратор",
        )
    user = await repo.get_admin_user_by_id(session, admin_user_id)
    if user is None:
        raise RecordNotFoundError("Admin user no longer exists")
    return CurrentAdminUser(admin_user_id=user.id, email=user.email, full_name=user.full_name)
