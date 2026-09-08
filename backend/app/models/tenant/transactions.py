from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.tenant.auth import Merchant, MerchantBalance
from app.models.tenant.base import TenantBase
from app.models.tenant.mediator import CompanyBalance, PaymentMethodCompany


class Transaction(TenantBase):
    __tablename__ = "transaction"
    __table_args__ = (UniqueConstraint("merchant_system_id", "merchant_id", name="uq_transaction_merchant_system"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tracker_id: Mapped[str] = mapped_column(String(255))
    partner_system_id: Mapped[str] = mapped_column(String(255))
    merchant_system_id: Mapped[str] = mapped_column(String(255))
    merchant_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(10))  # ACCEPTED / SUCCESS / DECLINED / APPEAL
    direction: Mapped[str] = mapped_column(String(3))  # IN / OUT
    date_create: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    date_update: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    callback_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    commission: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    partner_income: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    pure_our_income: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    amount_after_commission: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    p2p_card: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_addition_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    addition_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    original_tracker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    usdt_fixed_course: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    amount_after_commission_in_usdt: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchant.id"))
    payment_method_company_id: Mapped[int] = mapped_column(ForeignKey("payment_method_company.id"))

    merchant: Mapped[Merchant] = relationship()
    payment_method_company: Mapped[PaymentMethodCompany] = relationship()


class Settlements(TenantBase):
    __tablename__ = "settlement"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(8), default="ACCEPTED")
    settl_type: Mapped[str] = mapped_column(String(13))  # FROM_PARTNER / TO_MERCHANT / FROM_MERCHANT / TO_PARTNER
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    commission: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    our_funds: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    clients_funds: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    conversion_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    amount_in_usdt: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    final_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    final_amount_in_usdt: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    wallet: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tracker_link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # soft FK -> Transaction.tracker_id
    date_create: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    date_update: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tg_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    balance_merchant_id: Mapped[int] = mapped_column(ForeignKey("merchantbalance.id"))
    balance_partner_id: Mapped[int] = mapped_column(ForeignKey("company_balance.id"))

    balance_merchant: Mapped[MerchantBalance] = relationship()
    balance_partner: Mapped[CompanyBalance] = relationship()


class Appeal(TenantBase):
    """Disputes/chargebacks. Two of three monoliths define `AppealAdmin` but
    never register it with Django admin (see migration reports) — confirm
    with the team whether disputes are actually manageable today before
    surfacing this in the frontend nav."""

    __tablename__ = "appeal"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(8), default="APPEAL")
    description: Mapped[str] = mapped_column(String(255))
    type_appeal: Mapped[str] = mapped_column(String(255))
    photo: Mapped[str] = mapped_column(String(255))  # FileField -> stored path/URL
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transaction.id"), unique=True)

    transaction: Mapped[Transaction] = relationship()


class AntiFraudBlockedMerchantUsers(TenantBase):
    __tablename__ = "anti_fraud_blocked_merchant_users"
    __table_args__ = (UniqueConstraint("merchant_id", "user_id", name="uq_antifraud_merchant_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_name: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[str] = mapped_column(String(255))  # merchant's own external user id, not an FK
    second_chance: Mapped[bool] = mapped_column(default=False)
    second_chance_counter: Mapped[int] = mapped_column(default=0)
    second_chance_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ban_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date_create: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    date_update: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    permanent_ban: Mapped[bool] = mapped_column(default=False)
    merchant_id: Mapped[int | None] = mapped_column(ForeignKey("merchant.id", ondelete="SET NULL"), nullable=True)

    merchant: Mapped[Merchant | None] = relationship()
