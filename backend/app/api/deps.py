"""FastAPI dependency providers used by handlers in `app/api`.

These sit between the API layer and the DB layer: they turn a request's
Authorization header into a `CurrentUser`/`DecodedToken`, and turn that into
a DB session (control-plane or the correct tenant, resolved by `brand_id`).
Handlers depend on this module; nothing below it (services, repositories,
db) depends upward on it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import role_at_least
from app.core.roles import BrandRole
from app.core.security import DecodedToken, TokenError, TokenScope, decode_token
from app.db.control_plane import get_control_plane_session
from app.db.tenant_registry import get_tenant_sessionmaker

bearer_scheme = HTTPBearer(auto_error=True)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    admin_user_id: int
    brand_id: str
    role: str


async def get_pre_auth_claims(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> DecodedToken:
    try:
        return decode_token(credentials.credentials, TokenScope.PRE_AUTH)
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentUser:
    try:
        claims = decode_token(credentials.credentials, TokenScope.ACCESS)
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    if not claims.brand_id or not claims.role:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Access token missing brand_id/role")
    return CurrentUser(admin_user_id=int(claims.sub), brand_id=claims.brand_id, role=claims.role)


async def get_tenant_session(
    current_user: CurrentUser = Depends(get_current_user),
) -> AsyncIterator[AsyncSession]:
    """Yields a session bound to the current user's brand, in a read-only transaction.

    `SET TRANSACTION READ ONLY` is a defense-in-depth belt-and-suspenders
    measure: the generic admin engine (`app.repositories.admin_repository`)
    only ever issues SELECTs, but this guarantees that even a bug in a future
    extension can't accidentally mutate a tenant's production payments DB
    from what is meant to be a read-only surface.
    """
    sessionmaker = get_tenant_sessionmaker(current_user.brand_id)
    async with sessionmaker() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        yield session


async def get_tenant_write_session(
    current_user: CurrentUser = Depends(get_current_user),
) -> AsyncIterator[AsyncSession]:
    """Yields a WRITABLE session bound to the current user's brand.

    Deliberately a separate dependency from `get_tenant_session` (which
    opens `SET TRANSACTION READ ONLY`) rather than a flag on it — a route
    that wants to write must explicitly opt into this one, so "can this
    handler mutate a brand's production DB" is visible at the dependency
    list, not buried in a boolean. Always pair with `require_role(...)` on
    the same route; this dependency does not check permissions itself.
    """
    sessionmaker = get_tenant_sessionmaker(current_user.brand_id)
    async with sessionmaker() as session:
        yield session


def require_role(minimum: BrandRole):
    """Dependency factory: 403s unless the caller's role is >= `minimum`.

    Usage: `Depends(require_role(BrandRole.OPERATOR))` on a write route.
    """

    async def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not role_at_least(current_user.role, minimum):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Role '{current_user.role}' does not meet the required minimum '{minimum.value}'",
            )
        return current_user

    return _check


async def require_control_plane_session(
    session: AsyncSession = Depends(get_control_plane_session),
) -> AsyncSession:
    return session
