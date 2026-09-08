"""Data-access layer for the control-plane DB (staff auth, brand access, audit log).

Pure queries only — no business rules (e.g. "is a superuser allowed to see
every brand" lives in `app.services.auth_service`, not here). Depends only on
`app.models.control_plane`; never imports from `services` or `api`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.control_plane import AdminUser, AuditLog, BrandAccess


async def get_admin_user_by_email(session: AsyncSession, email: str) -> AdminUser | None:
    result = await session.execute(select(AdminUser).where(AdminUser.email == email))
    return result.scalar_one_or_none()


async def get_admin_user_by_id(session: AsyncSession, admin_user_id: int) -> AdminUser | None:
    result = await session.execute(select(AdminUser).where(AdminUser.id == admin_user_id))
    return result.scalar_one_or_none()


async def get_brand_access_list(session: AsyncSession, admin_user_id: int) -> list[BrandAccess]:
    result = await session.execute(select(BrandAccess).where(BrandAccess.admin_user_id == admin_user_id))
    return list(result.scalars().all())


async def get_brand_access(session: AsyncSession, admin_user_id: int, brand_id: str) -> BrandAccess | None:
    result = await session.execute(
        select(BrandAccess).where(
            BrandAccess.admin_user_id == admin_user_id, BrandAccess.brand_id == brand_id
        )
    )
    return result.scalar_one_or_none()


async def write_audit_log(
    session: AsyncSession,
    *,
    admin_user_id: int | None,
    brand_id: str | None,
    action: str,
    detail: dict | None = None,
    ip_address: str | None = None,
) -> None:
    session.add(
        AuditLog(
            admin_user_id=admin_user_id,
            brand_id=brand_id,
            action=action,
            detail=detail or {},
            ip_address=ip_address,
        )
    )
    await session.commit()
