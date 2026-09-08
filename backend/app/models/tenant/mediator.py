from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.tenant.auth import Merchant
from app.models.tenant.base import TenantBase
from app.models.tenant.client import Currency


class Company(TenantBase):
    __tablename__ = "company"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)


class PaymentMethod(TenantBase):
    __tablename__ = "payment_method"
    __table_args__ = (
        UniqueConstraint("name", "currency_id", "sub_method", "direction", name="uq_payment_method_combo"),
        UniqueConstraint("token", "direction", name="uq_payment_method_token_direction"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    sub_method: Mapped[str | None] = mapped_column(String(255), nullable=True)
    direction: Mapped[str] = mapped_column(String(3))  # IN / OUT
    token: Mapped[str] = mapped_column(String(255))
    currency_id: Mapped[int] = mapped_column(ForeignKey("currency.id"))

    currency: Mapped[Currency] = relationship()


class PaymentMethodCascade(TenantBase):
    __tablename__ = "payment_method_cascade"
    __table_args__ = (UniqueConstraint("name", "payment_method_id", name="uq_cascade_name_method"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    date_create: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    date_update: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payment_method_id: Mapped[int] = mapped_column(ForeignKey("payment_method.id"))

    payment_method: Mapped[PaymentMethod] = relationship()


class PaymentMethodTemplate(TenantBase):
    __tablename__ = "payment_method_template"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(3))
    default_personal_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    transaction_min_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    transaction_max_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    test_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    only_admin_configure: Mapped[bool] = mapped_column(Boolean, default=False)
    currency_id: Mapped[int] = mapped_column(ForeignKey("currency.id"))

    currency: Mapped[Currency] = relationship()


class PaymentMethodCompany(TenantBase):
    __tablename__ = "payment_method_company"
    __table_args__ = (UniqueConstraint("payment_method_id", "company_id", name="uq_pmc_method_company"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean)
    partner_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=0)
    additional_commission: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), default=0)
    settlement_commission: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), default=0)
    daily_amount_limit: Mapped[int | None] = mapped_column(nullable=True)
    daily_count_limit: Mapped[int | None] = mapped_column(nullable=True)
    transaction_min_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    transaction_max_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    current_daily_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    current_daily_amount_success: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    current_daily_count: Mapped[int] = mapped_column(default=0)
    current_daily_coun_success: Mapped[int] = mapped_column(default=0)  # sic: typo preserved from source schema
    last_reset: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    changing_rate: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(default=1)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"))
    payment_method_id: Mapped[int] = mapped_column(ForeignKey("payment_method.id"))

    company: Mapped[Company] = relationship()
    payment_method: Mapped[PaymentMethod] = relationship()


class MerchantPaymentMethod(TenantBase):
    __tablename__ = "merchant_payment_system"
    __table_args__ = (UniqueConstraint("merchant_id", "payment_method_id", name="uq_mpm_merchant_method"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    personal_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    additional_commission: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), default=0)
    test_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    block: Mapped[bool] = mapped_column(Boolean, default=False)
    no_callback: Mapped[bool] = mapped_column(Boolean, default=False)
    transaction_min_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    transaction_max_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    only_admin_configure: Mapped[bool] = mapped_column(Boolean, default=False)
    cascade_id: Mapped[int | None] = mapped_column(ForeignKey("payment_method_cascade.id"), nullable=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchant.id"))
    payment_method_id: Mapped[int] = mapped_column(ForeignKey("payment_method.id"))

    cascade: Mapped[PaymentMethodCascade | None] = relationship()
    merchant: Mapped[Merchant] = relationship()
    payment_method: Mapped[PaymentMethod] = relationship()


class CompanyBalance(TenantBase):
    __tablename__ = "company_balance"
    __table_args__ = (UniqueConstraint("company_id", "currency_id", name="uq_company_balance_company_currency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    available_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    blocked_balance_in: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    blocked_balance_out: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    our_income: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    clients_funds: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    settlement_commission: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    alert_balance_percent: Mapped[int | None] = mapped_column(nullable=True)
    insurance_balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"))
    currency_id: Mapped[int] = mapped_column(ForeignKey("currency.id"))

    company: Mapped[Company] = relationship()
    currency: Mapped[Currency] = relationship()


class CompanyMethodStatistics(TenantBase):
    """Mapped for completeness; source admin.py defines `CompanyMethodStatisticsAdmin`
    but never registers it (see migration reports) — confirm with the team whether
    this should actually be exposed before wiring it into the frontend nav."""

    __tablename__ = "company_method_statistics"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date]
    transaction_count: Mapped[int] = mapped_column(default=0)
    success_transaction_count: Mapped[int] = mapped_column(default=0)
    transaction_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    success_transaction_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    conversion_percentage: Mapped[int] = mapped_column(default=0)
    payment_method_company_id: Mapped[int] = mapped_column(ForeignKey("payment_method_company.id"))

    payment_method_company: Mapped[PaymentMethodCompany] = relationship()


class FloatedProcentsCompanies(TenantBase):
    __tablename__ = "floated_procents"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_amount: Mapped[Decimal] = mapped_column(Numeric(26, 2))
    to_amount: Mapped[Decimal] = mapped_column(Numeric(26, 2))
    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    payment_method_company_id: Mapped[int] = mapped_column(ForeignKey("payment_method_company.id"))

    payment_method_company: Mapped[PaymentMethodCompany] = relationship()


class TemplateMethodMapping(TenantBase):
    __tablename__ = "template_method_mapping"
    __table_args__ = (UniqueConstraint("template_id", "payment_method_id", name="uq_template_mapping"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_method_id: Mapped[int] = mapped_column(ForeignKey("payment_method.id"))
    template_id: Mapped[int] = mapped_column(ForeignKey("payment_method_template.id"))

    payment_method: Mapped[PaymentMethod] = relationship()
    template: Mapped[PaymentMethodTemplate] = relationship()


class PaymentMethodCascadeItem(TenantBase):
    __tablename__ = "payment_method_cascade_item"
    __table_args__ = (
        UniqueConstraint("cascade_id", "payment_method_company_id", name="uq_cascade_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    priority: Mapped[int]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    cascade_id: Mapped[int] = mapped_column(ForeignKey("payment_method_cascade.id"))
    payment_method_company_id: Mapped[int] = mapped_column(ForeignKey("payment_method_company.id"))

    cascade: Mapped[PaymentMethodCascade] = relationship()
    payment_method_company: Mapped[PaymentMethodCompany] = relationship()
