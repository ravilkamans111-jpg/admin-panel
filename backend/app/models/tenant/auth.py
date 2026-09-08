from __future__ import annotations

from decimal import Decimal

from sqlalchemy import JSON, Boolean, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.tenant.base import TenantBase
from app.models.tenant.client import Currency


class DjangoAuthUser(TenantBase):
    """Read-only mapping of Django's stock `auth_user` table.

    None of the three monoliths customize `AUTH_USER_MODEL` (confirmed in
    all three migration reports) — `Merchant.user` is a plain FK to this
    table. Only the columns needed to display "which login owns this
    merchant" are mapped; password hashes are intentionally NOT selected by
    any query this service issues (see admin_engine list_display configs).
    """

    __tablename__ = "auth_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(254))
    is_active: Mapped[bool] = mapped_column(Boolean)
    is_staff: Mapped[bool] = mapped_column(Boolean)
    is_superuser: Mapped[bool] = mapped_column(Boolean)
    date_joined: Mapped[str] = mapped_column(String(64))


class Merchant(TenantBase):
    __tablename__ = "merchant"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_merchant_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="TEST")
    public_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    private_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transaction_id: Mapped[int | None] = mapped_column(nullable=True)
    project_url: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("auth_user.id"))

    user: Mapped[DjangoAuthUser] = relationship()
    balances: Mapped[list[MerchantBalance]] = relationship(back_populates="merchant")


class WhiteList(TenantBase):
    __tablename__ = "white_list"

    id: Mapped[int] = mapped_column(primary_key=True)
    allowed_ip: Mapped[str] = mapped_column(String(100), unique=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchant.id"))

    merchant: Mapped[Merchant] = relationship()


class InviteToken(TenantBase):
    __tablename__ = "invite_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_name: Mapped[str] = mapped_column(String(100), unique=True)
    token: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    invited_user_id: Mapped[int | None] = mapped_column(ForeignKey("auth_user.id"), nullable=True)

    invited_user: Mapped[DjangoAuthUser | None] = relationship()


class MerchantBalance(TenantBase):
    __tablename__ = "merchantbalance"
    __table_args__ = (UniqueConstraint("merchant_id", "currency_id", name="uq_merchantbalance_merchant_currency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    blocked_balance_in: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    blocked_balance_out: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    settlement_commission: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    blocked_balance_usdt_in: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0)
    blocked_balance_usdt_out: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0)
    balance_usdt: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0)
    insurance_balance_usdt: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    currency_id: Mapped[int] = mapped_column(ForeignKey("currency.id"))
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchant.id"))

    currency: Mapped[Currency] = relationship()
    merchant: Mapped[Merchant] = relationship(back_populates="balances")


class UserConfig(TenantBase):
    __tablename__ = "user_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    enable_usdt_exchanger: Mapped[bool] = mapped_column(Boolean, default=False)
    binance_stack_page: Mapped[int | None] = mapped_column(nullable=True)
    binance_stack_rows: Mapped[int | None] = mapped_column(nullable=True)
    binance_stack_row_from: Mapped[int | None] = mapped_column(nullable=True)
    binance_stack_row_to: Mapped[int | None] = mapped_column(nullable=True)
    bybit_stack_page: Mapped[int | None] = mapped_column(nullable=True)
    bybit_stack_size: Mapped[int | None] = mapped_column(nullable=True)
    bybit_stack_row_from: Mapped[int | None] = mapped_column(nullable=True)
    bybit_stack_row_to: Mapped[int | None] = mapped_column(nullable=True)
    exchanger_source: Mapped[str] = mapped_column(String(32), default="global_cascade")
    pinned_exchange: Mapped[str | None] = mapped_column(String(16), nullable=True)
    manual_usdt_rates: Mapped[dict] = mapped_column(JSON, default=dict)
    user_id: Mapped[int] = mapped_column(ForeignKey("auth_user.id"), unique=True)

    user: Mapped[DjangoAuthUser] = relationship()
