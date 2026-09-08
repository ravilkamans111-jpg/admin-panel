from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.tenant.base import TenantBase
from app.models.tenant.mediator import PaymentMethod


class TestCredits(TenantBase):
    __tablename__ = "sandbox_testcredits"
    __table_args__ = (UniqueConstraint("payment_method_id", "requisite", name="uq_testcredits_method_requisite"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    requisite: Mapped[str] = mapped_column(String(256))
    wanted_status_callback: Mapped[str] = mapped_column(String(8), default="SUCCESS")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    requisite_details: Mapped[dict] = mapped_column(JSON, default=dict)
    payment_method_id: Mapped[int] = mapped_column(ForeignKey("payment_method.id"))

    payment_method: Mapped[PaymentMethod] = relationship()
