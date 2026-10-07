"""Двухшаговая аутентификация — как в админке монолита.

Логин — это логин Django: проверяем `username` + пароль по таблице `auth_user`
каждого бренда (PBKDF2, как у `django.contrib.auth.ModelBackend`), пропускаем
`is_active and is_staff` (условие входа в Django admin). Отдельной БД
пользователей у сервиса нет: пользователи, пароли и их деактивация — те же, что
в монолите.

Роль в бренде:
  - `is_superuser`                      -> superadmin (в Django — все права);
  - есть права add_/change_/delete_      -> operator   (может править);
  - иначе (только view_ или без прав)    -> viewer.

Токены без состояния. Refresh-токен несёт отпечаток хэша пароля — смена пароля
в монолите или деактивация пользователя отзывает сессии при ближайшем refresh.
Сервис сообщает домен-исключения из `app.core.exceptions`; перевод в HTTP — в
`app.api.auth`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.brands import KNOWN_BRANDS
from app.core.django_password import DUMMY_DJANGO_HASH, password_fingerprint, verify_django_password
from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    BrandAccessDeniedError,
    InvalidCredentialsError,
    InvalidTokenError,
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
)
from app.db.tenant_registry import get_tenant_sessionmaker
from app.repositories import django_auth_repository as users
from app.services import login_throttle

logger = logging.getLogger("audit")

WRITE_PERMISSION_PREFIXES = ("add_", "change_", "delete_")


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
    username: str
    brands: list[str]


async def _role_for(session: AsyncSession, user: users.DjangoStaffUser) -> str:
    if user.is_superuser:
        return BrandRole.SUPERADMIN.value
    codenames = await users.get_permission_codenames(session, user.id)
    if any(c.startswith(WRITE_PERMISSION_PREFIXES) for c in codenames):
        return BrandRole.OPERATOR.value
    return BrandRole.VIEWER.value


async def login(*, username: str, password: str, ip_address: str | None) -> LoginResult:
    username = username.strip()
    if await login_throttle.is_locked(username):
        logger.warning("login_locked username=%s ip=%s", username, ip_address)
        raise AccountLockedError

    options: list[BrandOption] = []
    brand_users: dict[str, int] = {}
    for brand_id, brand in KNOWN_BRANDS.items():
        try:
            async with get_tenant_sessionmaker(brand_id)() as session:
                user = await users.get_user_by_username(session, username)
                # Always one hash verification per brand, user or not (no timing oracle).
                password_ok = verify_django_password(password, user.password_hash if user else DUMMY_DJANGO_HASH)
                if user is not None and password_ok and user.can_use_admin:
                    brand_users[brand_id] = user.id
                    options.append(
                        BrandOption(brand_id=brand_id, display_name=brand.display_name, role=await _role_for(session, user))
                    )
        except Exception:
            logger.exception("login: brand %s unavailable", brand_id)

    if not options:
        await login_throttle.register_failure(username)
        logger.warning("login_failed username=%s ip=%s", username, ip_address)
        raise InvalidCredentialsError

    await login_throttle.clear(username)
    logger.info("login username=%s brands=%s ip=%s", username, sorted(brand_users), ip_address)
    return LoginResult(pre_auth_token=create_pre_auth_token(username, brand_users), available_brands=options)


async def _load_active_user(session: AsyncSession, user_id: int) -> users.DjangoStaffUser:
    user = await users.get_user_by_id(session, user_id)
    if user is None or not user.can_use_admin:
        raise AccountInactiveError
    return user


async def _issue(session: AsyncSession, user: users.DjangoStaffUser, brand_id: str) -> TokenPair:
    role = await _role_for(session, user)
    return TokenPair(
        access_token=create_access_token(user.id, brand_id, role),
        refresh_token=create_refresh_token(user.id, brand_id, role, password_fingerprint(user.password_hash)),
        brand_id=brand_id,
        role=role,
    )


async def select_brand(*, username: str, brand_users: dict[str, int], brand_id: str, ip_address: str | None) -> TokenPair:
    if brand_id not in KNOWN_BRANDS:
        raise UnknownBrandError(brand_id)
    user_id = brand_users.get(brand_id)
    if user_id is None:
        logger.warning("access_denied username=%s brand=%s ip=%s", username, brand_id, ip_address)
        raise BrandAccessDeniedError(brand_id)
    async with get_tenant_sessionmaker(brand_id)() as session:
        user = await _load_active_user(session, user_id)
        pair = await _issue(session, user, brand_id)
    logger.info("select_brand username=%s brand=%s role=%s ip=%s", username, brand_id, pair.role, ip_address)
    return pair


async def refresh_tokens(*, refresh_token: str) -> TokenPair:
    try:
        claims = decode_token(refresh_token, TokenScope.REFRESH)
    except TokenError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if not claims.brand_id or claims.brand_id not in KNOWN_BRANDS:
        raise AccountInactiveError
    async with get_tenant_sessionmaker(claims.brand_id)() as session:
        user = await _load_active_user(session, int(claims.sub))
        if claims.ver != password_fingerprint(user.password_hash):
            raise InvalidTokenError("Session revoked")
        return await _issue(session, user, claims.brand_id)


def describe_pre_auth(*, username: str, brand_users: dict[str, int]) -> CurrentAdminUser:
    return CurrentAdminUser(username=username, brands=sorted(brand_users))
