"""JWT issuance/verification for the two-step login.

1. POST /auth/login (username + password) -> the credentials are checked against
   each brand's own Django `auth_user` (see `app.services.auth_service`); a
   short-lived `pre_auth` token lists the brands they were valid in.
2. POST /auth/select-brand (pre_auth token + brand_id) -> issues a brand-scoped
   access+refresh token pair. Every later request carries `brand_id` in the
   access token; `app.api.deps` resolves the tenant DB session from it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt
from pydantic import BaseModel

from app.core.config import get_auth_secrets


class TokenScope(StrEnum):
    PRE_AUTH = "pre_auth"
    ACCESS = "access"
    REFRESH = "refresh"


class DecodedToken(BaseModel):
    sub: str  # admin_user id, as string
    scope: TokenScope
    brand_id: str | None = None
    role: str | None = None
    ver: str = ""
    brands: dict[str, int] = {}  # pre-auth only: brand_id -> that brand's auth_user.id
    jti: str


def _encode(payload: dict, expires_delta: timedelta) -> str:
    secrets = get_auth_secrets()
    now = datetime.now(UTC)
    to_encode = {
        **payload,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(to_encode, secrets.jwt_secret_key, algorithm=secrets.jwt_algorithm)


def create_pre_auth_token(username: str, brands: dict[str, int]) -> str:
    """`sub` is the login name; `brands` maps every brand the credentials were
    valid in to that brand's own `auth_user.id` (ids differ per brand DB)."""
    return _encode(
        {"sub": username, "scope": TokenScope.PRE_AUTH.value, "brands": brands},
        timedelta(minutes=5),
    )


def create_access_token(admin_user_id: int, brand_id: str, role: str) -> str:
    secrets = get_auth_secrets()
    return _encode(
        {"sub": str(admin_user_id), "scope": TokenScope.ACCESS.value, "brand_id": brand_id, "role": role},
        timedelta(minutes=secrets.access_token_expire_minutes),
    )


def create_refresh_token(admin_user_id: int, brand_id: str, role: str, version: str = "") -> str:
    secrets = get_auth_secrets()
    return _encode(
        {
            "sub": str(admin_user_id),
            "scope": TokenScope.REFRESH.value,
            "brand_id": brand_id,
            "role": role,
            "ver": version,
        },
        timedelta(days=secrets.refresh_token_expire_days),
    )


class TokenError(Exception):
    pass


def decode_token(token: str, expected_scope: TokenScope) -> DecodedToken:
    secrets = get_auth_secrets()
    try:
        payload = jwt.decode(token, secrets.jwt_secret_key, algorithms=[secrets.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Invalid token") from exc
    if payload.get("scope") != expected_scope.value:
        raise TokenError(f"Expected token scope '{expected_scope.value}', got '{payload.get('scope')}'")
    return DecodedToken(**payload)
