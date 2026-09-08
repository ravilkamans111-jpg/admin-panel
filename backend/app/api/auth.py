"""HTTP handlers for the two-step login flow.

Thin by design: parse the request, call `app.services.auth_service`, map its
domain exceptions to HTTP status codes, shape the response. No SQL, no JWT
encoding, no business rules live here — that's the service layer's job.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_pre_auth_claims
from app.core.exceptions import (
    AccountInactiveError,
    BrandAccessDeniedError,
    InvalidCredentialsError,
    InvalidTokenError,
    RecordNotFoundError,
    UnknownBrandError,
)
from app.core.security import DecodedToken
from app.db.control_plane import get_control_plane_session
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class BrandOption(BaseModel):
    brand_id: str
    display_name: str
    role: str


class LoginResponse(BaseModel):
    pre_auth_token: str
    available_brands: list[BrandOption]


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_control_plane_session),
) -> LoginResponse:
    try:
        result = await auth_service.login(
            session,
            email=body.email,
            password=body.password,
            ip_address=request.client.host if request.client else None,
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials") from exc

    return LoginResponse(
        pre_auth_token=result.pre_auth_token,
        available_brands=[BrandOption(brand_id=b.brand_id, display_name=b.display_name, role=b.role) for b in result.available_brands],
    )


class SelectBrandRequest(BaseModel):
    brand_id: str


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    brand_id: str
    role: str


@router.post("/select-brand", response_model=TokenPairResponse)
async def select_brand(
    body: SelectBrandRequest,
    request: Request,
    claims: DecodedToken = Depends(get_pre_auth_claims),
    session: AsyncSession = Depends(get_control_plane_session),
) -> TokenPairResponse:
    try:
        result = await auth_service.select_brand(
            session,
            admin_user_id=int(claims.sub),
            brand_id=body.brand_id,
            ip_address=request.client.host if request.client else None,
        )
    except AccountInactiveError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer active") from exc
    except UnknownBrandError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown brand '{body.brand_id}'") from exc
    except BrandAccessDeniedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No access to this brand") from exc

    return TokenPairResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        brand_id=result.brand_id,
        role=result.role,
    )


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_control_plane_session),
) -> TokenPairResponse:
    try:
        result = await auth_service.refresh_tokens(session, refresh_token=body.refresh_token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except AccountInactiveError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer active") from exc
    except BrandAccessDeniedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Brand access revoked") from exc

    return TokenPairResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        brand_id=result.brand_id,
        role=result.role,
    )


class MeResponse(BaseModel):
    admin_user_id: int
    email: str
    full_name: str


@router.get("/me", response_model=MeResponse)
async def me(
    claims: DecodedToken = Depends(get_pre_auth_claims),
    session: AsyncSession = Depends(get_control_plane_session),
) -> MeResponse:
    try:
        result = await auth_service.get_current_admin_user(session, admin_user_id=int(claims.sub))
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found") from exc

    return MeResponse(admin_user_id=result.admin_user_id, email=result.email, full_name=result.full_name)
