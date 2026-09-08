"""Control-plane schema: staff identity, brand access grants, audit log.

None of this exists in the source monoliths — each one authenticates admin
staff via Django's own session-based admin login against ITS OWN database,
with no concept of a user spanning brands (see migration reports §4: "no
row-level/tenant-scoped permission logic exists today"). This is genuinely
new infrastructure the multi-brand service needs, not a port.

Kept in its own database (not one of the three tenant DBs) so that staff
identity and access grants are independent of which brands exist, and so a
tenant DB can be swapped/restored without touching who's allowed to see it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.roles import BrandRole

__all__ = ["AdminUser", "AuditLog", "BrandAccess", "BrandRole", "ControlPlaneBase"]


class ControlPlaneBase(DeclarativeBase):
    pass


class AdminUser(ControlPlaneBase):
    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    brand_access: Mapped[list[BrandAccess]] = relationship(back_populates="admin_user")


class BrandAccess(ControlPlaneBase):
    __tablename__ = "brand_access"
    __table_args__ = (UniqueConstraint("admin_user_id", "brand_id", name="uq_brand_access_user_brand"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id", ondelete="CASCADE"))
    brand_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[BrandRole] = mapped_column(Enum(BrandRole), default=BrandRole.VIEWER)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    admin_user: Mapped[AdminUser] = relationship(back_populates="brand_access")


class AuditLog(ControlPlaneBase):
    """Records auth events and admin-surface access.

    Scope kept deliberately narrow for the read-only MVP: login, brand
    selection, and permission-denied attempts. Per-row detail-view logging
    is an intentional extension point for later, not implemented yet —
    logging every list/detail read on a payments admin is worth doing
    before write actions land, but adds volume that isn't justified yet.

    `admin_user_id` is deliberately NOT a foreign key (see migration 0002).
    Every write action in the service logs here with the acting
    `CurrentUser.admin_user_id` — including `HARDCODED_SUPERUSER_ID`
    (`app.services.auth_service`), which by design never has a matching
    `admin_user` row. A FK would reject every one of those writes.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    brand_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64))  # e.g. "login", "select_brand", "access_denied"
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
