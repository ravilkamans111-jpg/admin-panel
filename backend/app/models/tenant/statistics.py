from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.tenant.base import TenantBase
from app.models.tenant.mediator import MerchantPaymentMethod, PaymentMethod, PaymentMethodCompany


class ConversionStatisticsNew(TenantBase):
    __tablename__ = "conversion_statistics_new"
    __table_args__ = (UniqueConstraint("payment_method_id", "date_only", name="uq_convstat_method_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    num_requests: Mapped[int] = mapped_column(default=0)
    num_requests_success: Mapped[int] = mapped_column(default=0)
    num_paid_orders: Mapped[int] = mapped_column(default=0)
    amount_requests: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    amount_requests_success: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    amount_paid_orders: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    conversion_percent: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    conversion_percent_paid_orders: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    date_create: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_update: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    date_only: Mapped[date | None] = mapped_column(nullable=True)
    payment_method_id: Mapped[int] = mapped_column(ForeignKey("payment_method.id"))

    payment_method: Mapped[PaymentMethod] = relationship()


class ConversionStatisticsPartnersNew(TenantBase):
    __tablename__ = "conversion_statistics_partner_new"
    __table_args__ = (
        UniqueConstraint("payment_method_company_id", "date_only", name="uq_convstat_partner_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    num_requests: Mapped[int] = mapped_column(default=0)
    num_requests_success: Mapped[int] = mapped_column(default=0)
    num_paid_orders: Mapped[int] = mapped_column(default=0)
    amount_requests: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    amount_requests_success: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    amount_paid_orders: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    conversion_percent: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    conversion_percent_paid_orders: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    date_create: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_update: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    date_only: Mapped[date | None] = mapped_column(nullable=True)
    payment_method_company_id: Mapped[int] = mapped_column(ForeignKey("payment_method_company.id"))

    payment_method_company: Mapped[PaymentMethodCompany] = relationship()


class ConversionStatisticsMerchantNew(TenantBase):
    __tablename__ = "conversion_statistics_merchant_new"
    __table_args__ = (
        UniqueConstraint("merchant_payment_method_id", "date_only", name="uq_convstat_merchant_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    num_requests: Mapped[int] = mapped_column(default=0)
    num_requests_success: Mapped[int] = mapped_column(default=0)
    num_paid_orders: Mapped[int] = mapped_column(default=0)
    amount_requests: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    amount_requests_success: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    amount_paid_orders: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    conversion_percent: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    conversion_percent_paid_orders: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    date_create: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_update: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    date_only: Mapped[date | None] = mapped_column(nullable=True)
    merchant_payment_method_id: Mapped[int] = mapped_column(ForeignKey("merchant_payment_system.id"))

    merchant_payment_method: Mapped[MerchantPaymentMethod] = relationship()
