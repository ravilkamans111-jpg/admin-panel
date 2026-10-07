"""HTTP handlers for the two-step login flow.

Thin by design: parse the request, call `app.services.auth_service`, map its
domain exceptions to HTTP status codes, shape the response. Credentials are the
staff member's Django admin login (`auth_user` of each brand) — see the service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.deps import get_pre_auth_claims
from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    BrandAccessDeniedError,
    InvalidCredentialsError,
    InvalidTokenError,
    UnknownBrandError,
)
from app.core.security import DecodedToken
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


class LoginRequest(BaseModel):
    username: str
    password: str


class BrandOption(BaseModel):
    brand_id: str
    display_name: str
    role: str


class LoginResponse(BaseModel):
    pre_auth_token: str
    available_brands: list[BrandOption]


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    try:
        result = await auth_service.login(username=body.username, password=body.password, ip_address=_ip(request))
    except InvalidCredentialsError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials") from exc
    except AccountLockedError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много неудачных попыток входа. Повторите позже."
        ) from exc
    return LoginResponse(
        pre_auth_token=result.pre_auth_token,
        available_brands=[
            BrandOption(brand_id=b.brand_id, display_name=b.display_name, role=b.role) for b in result.available_brands
        ],
    )


class SelectBrandRequest(BaseModel):
    brand_id: str


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    brand_id: str
    role: str


def _pair(result: auth_service.TokenPair) -> TokenPairResponse:
    return TokenPairResponse(
        access_token=result.access_token, refresh_token=result.refresh_token,
        brand_id=result.brand_id, role=result.role,
    )


@router.post("/select-brand", response_model=TokenPairResponse)
async def select_brand(
    body: SelectBrandRequest, request: Request, claims: DecodedToken = Depends(get_pre_auth_claims)
) -> TokenPairResponse:
    try:
        result = await auth_service.select_brand(
            username=claims.sub, brand_users=claims.brands, brand_id=body.brand_id, ip_address=_ip(request)
        )
    except AccountInactiveError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer active") from exc
    except UnknownBrandError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown brand '{body.brand_id}'") from exc
    except BrandAccessDeniedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No access to this brand") from exc
    return _pair(result)


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh(body: RefreshRequest) -> TokenPairResponse:
    try:
        result = await auth_service.refresh_tokens(refresh_token=body.refresh_token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except AccountInactiveError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer active") from exc
    return _pair(result)


class MeResponse(BaseModel):
    username: str
    brands: list[str]


@router.get("/me", response_model=MeResponse)
async def me(claims: DecodedToken = Depends(get_pre_auth_claims)) -> MeResponse:
    described = auth_service.describe_pre_auth(username=claims.sub, brand_users=claims.brands)
    return MeResponse(username=described.username, brands=described.brands)
