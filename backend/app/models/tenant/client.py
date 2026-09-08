from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.tenant.base import TenantBase


class Bank(TenantBase):
    __tablename__ = "bank"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)


class Currency(TenantBase):
    __tablename__ = "currency"

    id: Mapped[int] = mapped_column(primary_key=True)
    iso_code: Mapped[str] = mapped_column(String(3), unique=True)
    addition_name: Mapped[str] = mapped_column(String(255))
    limit: Mapped[int] = mapped_column(default=0)
    binance_bank_id: Mapped[int | None] = mapped_column(ForeignKey("bank.id", ondelete="SET NULL"), nullable=True)

    binance_bank: Mapped[Bank | None] = relationship()


class Card(TenantBase):
    __tablename__ = "card"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    owner_name: Mapped[str] = mapped_column(String(255))
    requisite: Mapped[str] = mapped_column(String(255))
    transaction_count_limit: Mapped[int]
    transaction_amount_limit: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    current_transaction_count: Mapped[int]
    current_transaction_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    transaction_amount_lower_limit: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    transaction_amount_upper_limit: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    is_enabled: Mapped[bool]
    bank_id: Mapped[int | None] = mapped_column(ForeignKey("bank.id"), nullable=True)
    currency_id: Mapped[int] = mapped_column(ForeignKey("currency.id"))

    bank: Mapped[Bank | None] = relationship()
    currency: Mapped[Currency] = relationship()


class SellingInfo(TenantBase):
    __tablename__ = "selling_info"
    __table_args__ = (UniqueConstraint("currency_for_sale_id", "currency_for_buy", name="uq_selling_info_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    currency_for_buy: Mapped[str] = mapped_column(String(25), default="USDTTRC")
    coefficient: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    date_create: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    date_update: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    currency_for_sale_id: Mapped[int] = mapped_column(ForeignKey("currency.id"))

    currency_for_sale: Mapped[Currency] = relationship()
