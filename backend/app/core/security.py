"""Password hashing and JWT issuance/verification.

Two-step login, matching the TZ:
1. POST /auth/login (email+password) -> verifies credentials, returns a
   short-lived `pre_auth` token plus the list of brands this admin user can
   access. No brand_id yet: the admin hasn't chosen a workspace.
2. POST /auth/select-brand (pre_auth token + brand_id) -> verifies the user
   actually has BrandAccess to that brand_id, and issues a brand-scoped
   access+refresh token pair. Every subsequent request carries `brand_id` in
   the access token; tenancy.deps resolves the tenant DB session from it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import BaseModel

from app.core.config import get_auth_secrets

_hasher = PasswordHasher()


def hash_password(plain_password: str) -> str:
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain_password)
    except VerifyMismatchError:
        return False


class TokenScope(StrEnum):
    PRE_AUTH = "pre_auth"
    ACCESS = "access"
    REFRESH = "refresh"


class DecodedToken(BaseModel):
    sub: str  # admin_user id, as string
    scope: TokenScope
    brand_id: str | None = None
    role: str | None = None
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


def create_pre_auth_token(admin_user_id: int) -> str:
    return _encode(
        {"sub": str(admin_user_id), "scope": TokenScope.PRE_AUTH.value},
        timedelta(minutes=5),
    )


def create_access_token(admin_user_id: int, brand_id: str, role: str) -> str:
    secrets = get_auth_secrets()
    return _encode(
        {"sub": str(admin_user_id), "scope": TokenScope.ACCESS.value, "brand_id": brand_id, "role": role},
        timedelta(minutes=secrets.access_token_expire_minutes),
    )


def create_refresh_token(admin_user_id: int, brand_id: str, role: str) -> str:
    secrets = get_auth_secrets()
    return _encode(
        {"sub": str(admin_user_id), "scope": TokenScope.REFRESH.value, "brand_id": brand_id, "role": role},
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
