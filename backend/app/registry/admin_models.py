"""Generic, config-driven read-only admin surface.

Every model gets an `AdminModelConfig` instead of a hand-written route +
Django-style `ModelAdmin` subclass. This is the deliberate trade-off from
the "read-only first, full interface, room to grow" scope: it gives every
one of the ~28 admin-registered models in the source monoliths a working
list/detail/filter/search screen immediately, instead of blocking the whole
service on hand-porting business logic for all of them.

Where the source `admin.py` embedded real logic beyond display (cascade
validation, cache invalidation, bulk-provisioning actions, the settlement
save() that mutates a linked Transaction — see the migration research
reports) that logic is NOT reproduced here on purpose: this engine is
read-only. Those configs are annotated with a `notes` field pointing back at
what would need explicit, tested reimplementation before any write path is
added — that is the intended next phase, not something this module fakes.

Labels (`verbose_name`, `verbose_name_plural`, `app_label`, `notes`) are in
Russian — this is user-facing text the frontend renders as-is. `key` and
`app` stay as stable English/technical identifiers (URL slugs, Django app
labels) since they're wire identifiers, not display text.

This module belongs to the "registry" layer: it depends on `app.models.tenant`
(model classes) but nothing from `repositories`/`services`/`api` — those
layers depend on it, never the other way around.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import DeclarativeBase

from app.core.rbac import role_at_least
from app.core.roles import BrandRole
from app.models.tenant import (
    AntiFraudBlockedMerchantUsers,
    Bank,
    Card,
    Company,
    CompanyBalance,
    CompanyMethodStatistics,
    ConversionStatisticsMerchantNew,
    ConversionStatisticsNew,
    ConversionStatisticsPartnersNew,
    Currency,
    DjangoAuthUser,
    FloatedProcentsCompanies,
    InviteToken,
    Merchant,
    MerchantBalance,
    MerchantPaymentMethod,
    PaymentMethod,
    PaymentMethodCascade,
    PaymentMethodCascadeItem,
    PaymentMethodCompany,
    PaymentMethodTemplate,
    SellingInfo,
    Settlements,
    TemplateMethodMapping,
    TestCredits,
    Transaction,
    WhiteList,
)

# Человекочитаемые названия групп нав-меню — ключ совпадает с исходным
# Django app_label (техническим, не отображаемым напрямую).
APP_LABELS_RU: dict[str, str] = {
    "personal_account_transaction": "Транзакции и расчёты",
    "personal_account_auth": "Мерчанты и доступ",
    "api_mediator": "Платёжные партнёры и роутинг",
    "api_client": "Справочники и курсы",
    "conversion_statistic": "Статистика конверсии",
    "sandbox": "Песочница",
}


def _all_fields(model: type[DeclarativeBase]) -> list[str]:
    """Every mapped column of `model`, in declaration order.

    Used as `list_display` for every registered model so the list view shows
    every field the record has — including ones like `Card.requisite` or
    `Transaction.p2p_card` that a hand-picked subset previously omitted —
    matching what the source Django admin's changelist ultimately exposes
    once you account for `list_display` + the fact that every field is still
    reachable from the detail view.
    """
    return list(model.__table__.columns.keys())


SENSITIVE_MASK = "••••••••"
# Roles below this see `AdminModelConfig.sensitive_fields` as SENSITIVE_MASK.
SENSITIVE_MIN_ROLE = BrandRole.BRAND_ADMIN


@dataclass(frozen=True, slots=True)
class VirtualFilter:
    """A list filter on something that isn't a column of the listed model
    (e.g. a transaction's *partner company*, two joins away) — mirrors the
    source admin's `payment_method_company__company__name`-style filters.
    `target` is the registry key of the model the operator picks records of;
    `build(ids)` returns the WHERE clause for the listed model."""

    target: str
    build: Callable[[list[int]], Any]


@dataclass(frozen=True, slots=True)
class AdminModelConfig:
    key: str
    app: str
    model: type[DeclarativeBase]
    verbose_name: str
    verbose_name_plural: str
    list_display: list[str]
    list_filter: list[str] = field(default_factory=list)
    search_fields: list[str] = field(default_factory=list)
    default_ordering: list[str] = field(default_factory=list)
    pk_field: str = "id"
    # Opt-in, not opt-out: empty means "not writable via the admin engine at
    # all" — matches Django's own default-deny shape (a field is only
    # editable if a ModelAdmin explicitly exposes it). The write path
    # (app.services.*_write_service) is the only thing allowed to populate
    # this for a model, and only once its save() logic has been read and
    # ported from source, not guessed at.
    editable_fields: list[str] = field(default_factory=list)
    # Fields required/accepted on create but NOT via PATCH on an existing
    # row (e.g. Settlement's `settl_type`/`balance_merchant_id`/
    # `balance_partner_id` — immutable once a settlement exists, matching
    # the source admin locking them down post-creation).
    creatable_fields: list[str] = field(default_factory=list)
    creatable: bool = False
    deletable: bool = False
    # How to build a human-readable label for a row of THIS model when it's
    # used as the target of a foreign-key picker (see `fk_fields` below) —
    # a `{field}` template using only this model's OWN columns. Mirrors the
    # source Django model's `__str__` where that reduces to own-field-only
    # substitution (Currency, Bank, Company, PaymentMethod, DjangoAuthUser);
    # where source's `__str__` needs a cross-model join (Merchant,
    # PaymentMethodTemplate, PaymentMethodCascade, ...) this is a
    # deliberately simplified same-spirit label, not a byte-identical port.
    str_template: str | None = None
    # FK columns (present in editable_fields/creatable_fields) that should
    # render as a dropdown of real records instead of a raw id input —
    # maps this model's own FK column name to the REGISTRY KEY of the
    # model it points to. Only set for targets that have a `str_template`;
    # a handful of genuinely composite/ambiguous FKs (e.g.
    # PaymentMethodCompany, whose source `__str__` needs two joins) are
    # deliberately left as plain id inputs rather than faked.
    fk_fields: dict[str, str] = field(default_factory=dict)
    # Date/datetime columns offered as a from–to range filter
    # (`?<field>__gte=…&<field>__lte=…`), like the source's DateRangeFilter.
    date_filters: list[str] = field(default_factory=list)
    virtual_filters: dict[str, VirtualFilter] = field(default_factory=dict)
    # Columns whose value is masked for roles below `SENSITIVE_MIN_ROLE`
    # in list/detail responses (still writable; the mask placeholder is
    # ignored on save so a round-tripped form can't overwrite the secret).
    sensitive_fields: list[str] = field(default_factory=list)
    # Russian column/field captions (the source models' `verbose_name`s). Falls
    # back to a humanised field name in the UI where a field has no entry.
    field_labels: dict[str, str] = field(default_factory=dict)
    # Fields edited in place in the list (Django's `list_editable`): checkboxes /
    # number inputs per row with a single Save for the page.
    list_editable: list[str] = field(default_factory=list)
    # Bulk actions offered in the list's "Action:" dropdown, as (key, label).
    actions: list[tuple[str, str]] = field(default_factory=list)
    # Hidden from the navigation (still reachable by the pages that embed it).
    hidden: bool = False
    list_per_page: int = 20
    # Extra WHERE clause for the search box on things that aren't columns of this
    # model (e.g. a partner method's method token / company name): `term -> clause`,
    # OR-ed with the column search.
    extra_search: Callable[[str], Any] | None = None
    # Minimum role for writing this model through the generic engine (default: operator).
    # `users` needs superadmin — editing `is_superuser`/`is_staff` is privilege escalation.
    write_role: BrandRole = BrandRole.OPERATOR

    @property
    def app_label(self) -> str:
        return APP_LABELS_RU.get(self.app, self.app)

    @property
    def is_writable(self) -> bool:
        return bool(self.editable_fields) or self.creatable


_REGISTRY: dict[str, AdminModelConfig] = {}


def register(config: AdminModelConfig) -> AdminModelConfig:
    if config.key in _REGISTRY:
        raise ValueError(f"Duplicate admin registry key: {config.key}")
    _REGISTRY[config.key] = config
    return config


def get_config(key: str) -> AdminModelConfig | None:
    return _REGISTRY.get(key)


def all_configs() -> list[AdminModelConfig]:
    return list(_REGISTRY.values())


# ---------------------------------------------------------------------------
# personal_account_transaction — денежное ядро
# ---------------------------------------------------------------------------

register(
    AdminModelConfig(
        key="transactions",
        app="personal_account_transaction",
        model=Transaction,
        verbose_name="Транзакция",
        verbose_name_plural="Транзакции",
        # Matches source TransactionAdmin.list_display order (merchant/
        # partner columns right after id, like the original Django admin),
        # not _all_fields()'s raw column-declaration order.
        list_display=[
            "id", "merchant_id", "payment_method_company_id", "date_create", "date_update",
            "status", "direction", "partner_system_id", "merchant_system_id", "merchant_client_id",
            "amount", "usdt_fixed_course", "amount_after_commission_in_usdt", "commission",
            "partner_income", "pure_our_income", "amount_after_commission", "p2p_card", "tracker_id",
            "status_addition_info", "addition_info", "callback_url", "original_tracker_id",
        ],
        list_filter=["status", "direction", "merchant_id"],
        date_filters=["date_create", "date_update"],
        virtual_filters={
            "company_id": VirtualFilter(
                "companies",
                lambda ids: Transaction.payment_method_company_id.in_(
                    select(PaymentMethodCompany.id).where(PaymentMethodCompany.company_id.in_(ids))
                ),
            ),
            "payment_method_id": VirtualFilter(
                "payment-methods",
                lambda ids: Transaction.payment_method_company_id.in_(
                    select(PaymentMethodCompany.id).where(PaymentMethodCompany.payment_method_id.in_(ids))
                ),
            ),
            "currency_id": VirtualFilter(
                "currencies",
                lambda ids: Transaction.payment_method_company_id.in_(
                    select(PaymentMethodCompany.id)
                    .join(PaymentMethod, PaymentMethod.id == PaymentMethodCompany.payment_method_id)
                    .where(PaymentMethod.currency_id.in_(ids))
                ),
            ),
        },
        search_fields=["tracker_id", "partner_system_id", "merchant_system_id", "merchant_client_id"],
        default_ordering=["-date_create"],
        # Matches TransactionAdminForm on EDIT (not create) — usdt_fixed_course
        # and amount_after_commission_in_usdt are swapped to read-only display
        # fields on edit in the source, so they're excluded here too. Editing
        # any of these runs the real balance state machine — see
        # `app.services.transaction_write_service`, not a plain column UPDATE.
        editable_fields=[
            "status", "amount", "commission", "partner_income", "pure_our_income",
            "amount_after_commission", "callback_url", "p2p_card",
            "status_addition_info", "addition_info", "original_tracker_id", "merchant_client_id",
        ],
        # merchant_id isn't editable (set once, never re-pointed to a
        # different merchant) so this never drives a write-picker — it's
        # here purely so list/detail views can show the merchant's name
        # instead of a bare id (mirrors source list_display's 'merchant').
        # payment_method_company_id's label (source's
        # 'payment_method_company_company', the "partner"/company name) needs
        # a two-hop join a single-table str_template can't express, so it's
        # handled by the transactions-only `_enrich_transaction_labels` in
        # app.repositories.admin_repository instead of fk_fields.
        fk_fields={"merchant_id": "merchants"},
        actions=[("send_callbacks", "Отправить коллбэки выбранным мерчантам")],
    )
)

register(
    AdminModelConfig(
        key="settlements",
        app="personal_account_transaction",
        model=Settlements,
        verbose_name="Сеттлмент",
        verbose_name_plural="Сеттлменты",
        # Same columns and order as the source SettlementsAdmin.list_display.
        list_display=[
            "id", "status", "settl_type", "balance_partner_id", "balance_merchant_id", "amount", "commission",
            "our_funds", "clients_funds", "conversion_rate", "amount_in_usdt", "final_amount",
            "final_amount_in_usdt", "wallet", "tracker_link", "date_create", "date_update", "transaction_id",
        ],
        list_filter=["status", "settl_type"],
        date_filters=["date_create", "date_update"],
        virtual_filters={
            "partner_company_id": VirtualFilter(
                "companies",
                lambda ids: Settlements.balance_partner_id.in_(select(CompanyBalance.id).where(CompanyBalance.company_id.in_(ids))),
            ),
            "partner_currency_id": VirtualFilter(
                "currencies",
                lambda ids: Settlements.balance_partner_id.in_(select(CompanyBalance.id).where(CompanyBalance.currency_id.in_(ids))),
            ),
            "merchant_currency_id": VirtualFilter(
                "currencies",
                lambda ids: Settlements.balance_merchant_id.in_(select(MerchantBalance.id).where(MerchantBalance.currency_id.in_(ids))),
            ),
            "merchant_id": VirtualFilter(
                "merchants",
                lambda ids: Settlements.balance_merchant_id.in_(select(MerchantBalance.id).where(MerchantBalance.merchant_id.in_(ids))),
            ),
        },
        search_fields=["status", "settl_type", "amount", "wallet", "tracker_link", "date_create", "date_update"],
        default_ordering=["-date_create"],
        editable_fields=[
            "status", "amount", "commission", "our_funds", "clients_funds",
            "conversion_rate", "amount_in_usdt", "final_amount", "final_amount_in_usdt",
            "wallet", "tracker_link", "tg_id",
        ],
        # Immutable once a settlement exists — matches the source admin's
        # settl_type-driven field-visibility state machine locking these down
        # post-creation (see migration research on SettlementsAdmin).
        creatable_fields=["settl_type", "balance_merchant_id", "balance_partner_id"],
        creatable=True,
        actions=[("send_callbacks", "Отправить коллбэки выбранным пользователям в телеграмм")],
        field_labels={
            "id": "PK", "status": "Статус", "settl_type": "Тип сеттла", "balance_partner_id": "Баланс партнера",
            "balance_merchant_id": "Баланс клиента", "amount": "Сумма валюте баланса",
            "commission": "% комиссии (из баланса!)", "our_funds": "Наши средства",
            "clients_funds": "Средства клиентов", "conversion_rate": "Курс обмена", "amount_in_usdt": "Сумма в USDT",
            "final_amount": "Итоговая сумма", "final_amount_in_usdt": "Итоговая сумма в USDT",
            "wallet": "Адрес кошелька", "tracker_link": "Ссылка на трекер",
            "transaction_id": "ID связанной транзакции в БД", "date_create": "Дата создания",
            "date_update": "Дата обновления", "tg_id": "Telegram ID клиента",
            "partner_company_id": "Компания партнёра", "partner_currency_id": "Валюта партнёра",
            "merchant_currency_id": "Валюта клиента", "merchant_id": "Мерчант клиента",
        },
    )
)

register(
    AdminModelConfig(
        key="antifraud-blocks",
        app="personal_account_transaction",
        model=AntiFraudBlockedMerchantUsers,
        verbose_name="Антифрод-блокировка",
        verbose_name_plural="Антифрод-блокировки",
        # Same columns and order as the source AntiFraudBlockedMerchantUsersAdmin.list_display.
        list_display=[
            "merchant_name", "user_id", "second_chance", "second_chance_counter", "second_chance_date",
            "ban_date", "date_create", "date_update", "permanent_ban",
        ],
        list_filter=["merchant_name"],
        date_filters=["second_chance_date", "date_create"],
        search_fields=["merchant_name", "user_id", "date_create", "date_update", "second_chance_date", "second_chance_counter"],
        default_ordering=["-date_create"],
        editable_fields=["merchant_id", "user_id", "second_chance", "second_chance_counter", "permanent_ban"],
        # The add form of the source admin: merchant, user id, second chance, permanent ban
        # (the counter and the dates are read-only / set by the save logic).
        creatable_fields=["merchant_id", "user_id", "second_chance", "permanent_ban"],
        creatable=True,
        deletable=True,
        list_editable=["second_chance", "second_chance_counter", "permanent_ban"],
        field_labels={
            "merchant_id": "Мерчант", "merchant_name": "Название мерчанта", "user_id": "ID пользователя",
            "second_chance": "Второй шанс", "second_chance_counter": "Счётчик вторых шансов",
            "second_chance_date": "Время второго шанса", "ban_date": "Дата бана",
            "date_create": "Дата создания", "date_update": "Дата обновления", "permanent_ban": "Постоянный бан",
        },
        fk_fields={"merchant_id": "merchants"},
    )
)

# ---------------------------------------------------------------------------
# personal_account_auth — мерчанты / балансы / доступ
# ---------------------------------------------------------------------------

register(
    AdminModelConfig(
        key="merchants",
        app="personal_account_auth",
        model=Merchant,
        verbose_name="Мерчант",
        verbose_name_plural="Мерчанты",
        list_display=_all_fields(Merchant),
        list_filter=["user_id"],
        search_fields=["name", "public_key", "project_url"],
        sensitive_fields=["private_key"],
        editable_fields=["name", "public_key", "private_key", "transaction_id", "project_url"],
        creatable_fields=["name", "public_key", "private_key", "transaction_id", "project_url", "user_id"],
        creatable=True,
        deletable=True,
        # Matches source's real `Merchant.__str__` (`f'{user.username}: {name}'`)
        # exactly, not the earlier simplified own-fields-only version — see
        # `_enrich_fk_labels`'s one-level recursion in admin_repository.py,
        # which is what makes `{user_id_label}` (itself derived from
        # `fk_fields={"user_id": "users"}` below) available to substitute here.
        str_template="{user_id_label}: {name}",
        fk_fields={"user_id": "users"},
    )
)

register(
    AdminModelConfig(
        key="merchant-balances",
        app="personal_account_auth",
        model=MerchantBalance,
        verbose_name="Баланс мерчанта",
        verbose_name_plural="Балансы мерчантов",
        # Matches source MerchantBalanceAdmin.list_display order
        # (merchant/currency columns right after id). Drops the two
        # computed-only display methods (get_conversion_coefficients,
        # available_balance_in_usdt) — not real columns here.
        list_display=[
            "id", "merchant_id", "currency_id", "balance", "balance_usdt", "insurance_balance_usdt",
            "blocked_balance_in", "blocked_balance_out", "blocked_balance_usdt_in", "blocked_balance_usdt_out",
            "settlement_commission",
        ],
        list_filter=["merchant_id", "currency_id"],
        default_ordering=["-id"],
        editable_fields=[
            "balance",
            "blocked_balance_in",
            "blocked_balance_out",
            "settlement_commission",
            "balance_usdt",
            "insurance_balance_usdt",
            "currency_id",
            "merchant_id",
        ],
        creatable_fields=[
            "balance",
            "blocked_balance_in",
            "blocked_balance_out",
            "settlement_commission",
            "balance_usdt",
            "insurance_balance_usdt",
            "currency_id",
            "merchant_id",
        ],
        creatable=True,
        deletable=True,
        fk_fields={"currency_id": "currencies", "merchant_id": "merchants"},
    )
)

register(
    AdminModelConfig(
        key="whitelist",
        app="personal_account_auth",
        model=WhiteList,
        verbose_name="Разрешённый IP",
        verbose_name_plural="Разрешённые IP",
        list_display=_all_fields(WhiteList),
        list_filter=["merchant_id"],
        search_fields=["allowed_ip"],
        editable_fields=["allowed_ip", "merchant_id"],
        creatable_fields=["allowed_ip", "merchant_id"],
        creatable=True,
        deletable=True,
        fk_fields={"merchant_id": "merchants"},
    )
)

register(
    AdminModelConfig(
        key="invite-tokens",
        app="personal_account_auth",
        model=InviteToken,
        verbose_name="Инвайт-токен",
        verbose_name_plural="Инвайт-токены",
        list_display=_all_fields(InviteToken),
        list_filter=["used"],
        search_fields=["client_name"],
        # `invited_user` не входит в `fields` у InviteTokenAdmin в источнике —
        # редактируется только через реальный flow приглашения, не вручную.
        editable_fields=["client_name", "token", "used"],
        creatable_fields=["client_name", "token", "used"],
        creatable=True,
        deletable=True,
    )
)

register(
    AdminModelConfig(
        key="users",
        app="personal_account_auth",
        model=DjangoAuthUser,
        verbose_name="Пользователь",
        verbose_name_plural="Пользователи",
        list_display=["username", "email", "first_name", "last_name", "is_staff"],  # Django's default user changelist
        list_filter=["is_active", "is_staff", "is_superuser"],
        search_fields=["username", "email", "first_name", "last_name"],
        str_template="{username}",
        default_ordering=["username"],
        editable_fields=["first_name", "last_name", "email", "is_active", "is_staff", "is_superuser"],
        field_labels={
            "id": "ID", "username": "Username", "first_name": "First name", "last_name": "Last name",
            "email": "Email address", "is_active": "Active", "is_staff": "Staff status",
            "is_superuser": "Superuser status", "last_login": "Last login", "date_joined": "Date joined",
        },
        write_role=BrandRole.SUPERADMIN,
        creatable=True,  # via the dedicated Django-style "Add user" endpoint (app.api.django_users)
    )
)

# ---------------------------------------------------------------------------
# api_mediator — платёжный роутинг и настройка партнёров
# ---------------------------------------------------------------------------

register(
    AdminModelConfig(
        key="companies",
        app="api_mediator",
        model=Company,
        verbose_name="Компания-партнёр",
        verbose_name_plural="Компании-партнёры",
        list_display=_all_fields(Company),
        search_fields=["name"],
        editable_fields=["name"],
        creatable_fields=["name"],
        creatable=True,
        deletable=True,
        str_template="{name}",
    )
)

register(
    AdminModelConfig(
        key="payment-methods",
        app="api_mediator",
        model=PaymentMethod,
        verbose_name="Платёжный метод",
        verbose_name_plural="Платёжные методы",
        list_display=_all_fields(PaymentMethod),
        list_filter=["direction", "currency_id"],
        search_fields=["name", "sub_method", "token"],
        editable_fields=["name", "sub_method", "direction", "token", "currency_id"],
        creatable_fields=["name", "sub_method", "direction", "token", "currency_id"],
        creatable=True,
        deletable=True,
        str_template="[{id}] {direction}: {token}",
        fk_fields={"currency_id": "currencies"},
    )
)

register(
    AdminModelConfig(
        key="payment-method-companies",
        app="api_mediator",
        model=PaymentMethodCompany,
        verbose_name="Платежный метод компании",
        verbose_name_plural="Платежные методы компаний-партнеров",
        # Matches source PaymentMethodCompanyAdmin.list_display order
        # (payment_method/company columns right after id).
        list_display=[
            "id", "payment_method_id", "company_id", "is_active", "priority", "partner_rate",
            "changing_rate", "additional_commission", "settlement_commission", "daily_amount_limit",
            "daily_count_limit", "transaction_min_limit", "transaction_max_limit", "current_daily_count",
            "current_daily_coun_success", "current_daily_amount", "current_daily_amount_success", "last_reset",
        ],
        list_filter=["is_active", "company_id", "payment_method_id"],
        default_ordering=["-is_active", "payment_method_id"],
        editable_fields=[
            "is_active",
            "priority",
            "partner_rate",
            "additional_commission",
            "settlement_commission",
            "daily_amount_limit",
            "daily_count_limit",
            "transaction_min_limit",
            "transaction_max_limit",
        ],
        # Read-only enrichment (neither field is in editable_fields, so this
        # never drives a write-picker) — just so list/detail views show the
        # partner company and method name instead of bare ids, matching
        # source list_display's `payment_method`/`company` columns.
        # Label when this record is itself a picker/filter target (cascade
        # items, floated percents, partner statistics): "<method> — <partner>".
        str_template="{payment_method_id_label} — {company_id_label}",
        fk_fields={"company_id": "companies", "payment_method_id": "payment-methods"},
        list_editable=[
            "is_active", "priority", "partner_rate", "additional_commission", "settlement_commission",
            "daily_amount_limit", "daily_count_limit", "transaction_min_limit", "transaction_max_limit",
        ],
        extra_search=lambda term: or_(
            PaymentMethodCompany.payment_method_id.in_(
                select(PaymentMethod.id).where(
                    or_(PaymentMethod.token.ilike(f"%{term}%"), PaymentMethod.name.ilike(f"%{term}%"))
                )
            ),
            PaymentMethodCompany.company_id.in_(select(Company.id).where(Company.name.ilike(f"%{term}%"))),
        ),
        field_labels={
            "id": "ID", "payment_method_id": "Платежный метод", "company_id": "Компания-партнёр",
            "is_active": "Статус активности", "priority": "Приоритет в каскаде",
            "partner_rate": "Процентная ставка (0.01 - 100)", "changing_rate": "Процентная ставка изменяется",
            "additional_commission": "Добавочная комиссия", "settlement_commission": "Сетлмент комиссия",
            "daily_amount_limit": "Дневной лимит на сумму", "daily_count_limit": "Дневной лимит на число транзакций",
            "transaction_min_limit": "Минимум транзакции", "transaction_max_limit": "Максимум транзакции",
            "current_daily_amount": "Сумма запросов сегодня",
            "current_daily_amount_success": "Сумма успешных транзакций сегодня",
            "current_daily_count": "Число запросов сегодня",
            "current_daily_coun_success": "Число успешных транзакций сегодня",
            "last_reset": "Время последнего обновления",
        },
    )
)

register(
    AdminModelConfig(
        key="merchant-payment-methods",
        app="api_mediator",
        model=MerchantPaymentMethod,
        verbose_name="Платёжный метод мерчанта",
        verbose_name_plural="Платёжные методы мерчантов",
        # Matches source MerchantPaymentMethodAdmin.list_display order
        # (merchant/payment_method columns right after id).
        list_display=[
            "id", "merchant_id", "payment_method_id", "personal_rate", "test_mode",
            "additional_commission", "block", "no_callback", "transaction_min_limit",
            "transaction_max_limit", "only_admin_configure", "cascade_id",
        ],
        list_filter=["test_mode", "block", "merchant_id", "payment_method_id"],
        editable_fields=[
            "personal_rate",
            "test_mode",
            "additional_commission",
            "block",
            "transaction_min_limit",
            "transaction_max_limit",
            "no_callback",
            "only_admin_configure",
            "cascade_id",
        ],
        # merchant_id/payment_method_id aren't editable (immutable after
        # creation) so, like Transaction.merchant_id, these only drive read
        # enrichment — list/detail views showing the merchant and method
        # name instead of bare ids, matching source list_display.
        str_template="{merchant_id_label} — {payment_method_id_label}",
        fk_fields={
            "cascade_id": "payment-method-cascades",
            "merchant_id": "merchants",
            "payment_method_id": "payment-methods",
        },
    )
)

register(
    AdminModelConfig(
        key="company-balances",
        app="api_mediator",
        model=CompanyBalance,
        verbose_name="Баланс компании",
        verbose_name_plural="Балансы компаний",
        # Matches source CompanyBalanceAdmin.list_display order (company/
        # currency columns right after id). Drops the three computed-only
        # display methods (get_conversion_coefficients,
        # available_balance_in_usdt, our_balance_in_usdt,
        # clients_founds_in_usdt) — not real columns here.
        list_display=[
            "id", "company_id", "currency_id", "blocked_balance_in", "blocked_balance_out",
            "available_balance", "our_income", "clients_funds", "alert_balance_percent",
            "insurance_balance", "settlement_commission",
        ],
        list_filter=["company_id", "currency_id"],
        editable_fields=[
            "available_balance",
            "blocked_balance_in",
            "blocked_balance_out",
            "our_income",
            "clients_funds",
            "settlement_commission",
            "alert_balance_percent",
            "insurance_balance",
            "company_id",
            "currency_id",
        ],
        creatable_fields=[
            "available_balance",
            "blocked_balance_in",
            "blocked_balance_out",
            "our_income",
            "clients_funds",
            "settlement_commission",
            "alert_balance_percent",
            "insurance_balance",
            "company_id",
            "currency_id",
        ],
        creatable=True,
        deletable=True,
        fk_fields={"company_id": "companies", "currency_id": "currencies"},
    )
)

register(
    AdminModelConfig(
        key="company-method-statistics",
        app="api_mediator",
        model=CompanyMethodStatistics,
        verbose_name="Статистика метода компании",
        verbose_name_plural="Статистика методов компаний",
        list_display=_all_fields(CompanyMethodStatistics),
        list_filter=["payment_method_company_id"],
        default_ordering=["-date"],
        fk_fields={"payment_method_company_id": "payment-method-companies"},
    )
)

register(
    AdminModelConfig(
        key="floated-percents",
        app="api_mediator",
        model=FloatedProcentsCompanies,
        verbose_name="Плавающая комиссия",
        verbose_name_plural="Плавающие комиссии",
        list_display=_all_fields(FloatedProcentsCompanies),
        list_filter=["payment_method_company_id"],
        editable_fields=["from_amount", "to_amount", "rate", "payment_method_company_id"],
        creatable_fields=["from_amount", "to_amount", "rate", "payment_method_company_id"],
        creatable=True,
        deletable=True,
        fk_fields={"payment_method_company_id": "payment-method-companies"},
    )
)

register(
    AdminModelConfig(
        key="payment-method-templates",
        app="api_mediator",
        model=PaymentMethodTemplate,
        verbose_name="Шаблон платёжного метода",
        verbose_name_plural="Шаблоны платёжных методов",
        list_display=_all_fields(PaymentMethodTemplate),
        search_fields=["name", "description"],
        editable_fields=[
            "name",
            "description",
            "currency_id",
            "direction",
            "transaction_min_limit",
            "transaction_max_limit",
            "test_mode",
            "only_admin_configure",
            "default_personal_rate",
        ],
        creatable_fields=[
            "name",
            "description",
            "currency_id",
            "direction",
            "transaction_min_limit",
            "transaction_max_limit",
            "test_mode",
            "only_admin_configure",
            "default_personal_rate",
        ],
        creatable=True,
        deletable=True,
        str_template="{name} — {default_personal_rate}%",
        fk_fields={"currency_id": "currencies"},
    )
)

register(
    AdminModelConfig(
        key="template-method-mappings",
        app="api_mediator",
        model=TemplateMethodMapping,
        verbose_name="Привязка метода к шаблону",
        verbose_name_plural="Привязки методов к шаблонам",
        list_display=_all_fields(TemplateMethodMapping),
        list_filter=["template_id"],
        editable_fields=["payment_method_id", "template_id"],
        creatable_fields=["payment_method_id", "template_id"],
        creatable=True,
        deletable=True,
        fk_fields={"payment_method_id": "payment-methods", "template_id": "payment-method-templates"},
    )
)

register(
    AdminModelConfig(
        key="payment-method-cascades",
        app="api_mediator",
        model=PaymentMethodCascade,
        verbose_name="Каскад платёжных методов",
        verbose_name_plural="Каскады платёжных методов",
        list_display=_all_fields(PaymentMethodCascade),
        list_filter=["is_active", "payment_method_id"],
        search_fields=["name", "description"],
        editable_fields=["name", "description", "is_active"],
        creatable_fields=["name", "payment_method_id", "description", "is_active"],
        creatable=True,
        str_template="{name}",
        fk_fields={"payment_method_id": "payment-methods"},
    )
)

register(
    AdminModelConfig(
        key="payment-method-cascade-items",
        app="api_mediator",
        model=PaymentMethodCascadeItem,
        verbose_name="Элемент каскада",
        verbose_name_plural="Элементы каскада",
        list_display=_all_fields(PaymentMethodCascadeItem),
        list_filter=["cascade_id", "is_active"],
        default_ordering=["priority"],
        editable_fields=["payment_method_company_id", "priority", "is_active"],
        # `creatable` stays False on purpose: items are created from the cascade page
        # (POST /admin/payment-method-cascades/{id}/items), which runs the method-match /
        # unique-priority validation that the generic create endpoint would skip.
        # `creatable_fields` is still the field allowlist that dedicated service uses.
        creatable_fields=["cascade_id", "payment_method_company_id", "priority", "is_active"],
        deletable=True,
        fk_fields={"cascade_id": "payment-method-cascades", "payment_method_company_id": "payment-method-companies"},
        hidden=True,
    )
)

# ---------------------------------------------------------------------------
# api_client — справочники (валюты, банки, карты, курсы)
# ---------------------------------------------------------------------------

register(
    AdminModelConfig(
        key="currencies",
        app="api_client",
        model=Currency,
        verbose_name="Валюта",
        verbose_name_plural="Валюты",
        list_display=_all_fields(Currency),
        search_fields=["iso_code", "addition_name"],
        editable_fields=["iso_code", "addition_name", "limit", "binance_bank_id"],
        creatable_fields=["iso_code", "addition_name", "limit", "binance_bank_id"],
        creatable=True,
        deletable=True,
        str_template="{iso_code}",
        fk_fields={"binance_bank_id": "banks"},
    )
)

register(
    AdminModelConfig(
        key="banks",
        app="api_client",
        model=Bank,
        verbose_name="Банк",
        verbose_name_plural="Банки",
        list_display=_all_fields(Bank),
        search_fields=["name"],
        editable_fields=["name"],
        creatable_fields=["name"],
        creatable=True,
        deletable=True,
        str_template="{name}",
    )
)

register(
    AdminModelConfig(
        key="cards",
        app="api_client",
        model=Card,
        verbose_name="Карта",
        verbose_name_plural="Карты",
        list_display=_all_fields(Card),
        list_filter=["is_enabled", "bank_id", "currency_id"],
        search_fields=["name", "owner_name", "requisite"],
        editable_fields=[
            "name",
            "owner_name",
            "requisite",
            "transaction_count_limit",
            "transaction_amount_limit",
            "current_transaction_count",
            "current_transaction_amount",
            "transaction_amount_lower_limit",
            "transaction_amount_upper_limit",
            "is_enabled",
            "bank_id",
            "currency_id",
        ],
        creatable_fields=[
            "name",
            "owner_name",
            "requisite",
            "transaction_count_limit",
            "transaction_amount_limit",
            "current_transaction_count",
            "current_transaction_amount",
            "transaction_amount_lower_limit",
            "transaction_amount_upper_limit",
            "is_enabled",
            "bank_id",
            "currency_id",
        ],
        creatable=True,
        deletable=True,
        fk_fields={"bank_id": "banks", "currency_id": "currencies"},
    )
)

register(
    AdminModelConfig(
        key="selling-info",
        app="api_client",
        model=SellingInfo,
        verbose_name="Курс обмена",
        verbose_name_plural="Курсы обмена",
        list_display=_all_fields(SellingInfo),
        list_filter=["currency_for_sale_id", "currency_for_buy"],
        default_ordering=["-date_update"],
        # date_update — Django `auto_now=True` в источнике, не входит в форму
        # редактирования; здесь тоже не в editable_fields и трогается
        # автоматически (см. app.services.generic_write_service).
        editable_fields=["currency_for_sale_id", "currency_for_buy", "coefficient", "date_create"],
        creatable_fields=["currency_for_sale_id", "currency_for_buy", "coefficient", "date_create"],
        creatable=True,
        deletable=True,
        fk_fields={"currency_for_sale_id": "currencies"},
    )
)

# ---------------------------------------------------------------------------
# conversion_statistic — отчётные ролл-апы (в исходнике тоже read-only)
# ---------------------------------------------------------------------------

register(
    AdminModelConfig(
        key="conversion-stats-by-method",
        app="conversion_statistic",
        model=ConversionStatisticsNew,
        verbose_name="Конверсия по методу",
        verbose_name_plural="Конверсия по методам",
        list_display=_all_fields(ConversionStatisticsNew),
        list_filter=["payment_method_id"],
        date_filters=["date_only"],
        default_ordering=["-date_only"],
        fk_fields={"payment_method_id": "payment-methods"},
    )
)

register(
    AdminModelConfig(
        key="conversion-stats-by-partner",
        app="conversion_statistic",
        model=ConversionStatisticsPartnersNew,
        verbose_name="Конверсия по партнёру",
        verbose_name_plural="Конверсия по партнёрам",
        list_display=_all_fields(ConversionStatisticsPartnersNew),
        list_filter=["payment_method_company_id"],
        date_filters=["date_only"],
        default_ordering=["-date_only"],
        fk_fields={"payment_method_company_id": "payment-method-companies"},
    )
)

register(
    AdminModelConfig(
        key="conversion-stats-by-merchant",
        app="conversion_statistic",
        model=ConversionStatisticsMerchantNew,
        verbose_name="Конверсия по мерчанту",
        verbose_name_plural="Конверсия по мерчантам",
        list_display=_all_fields(ConversionStatisticsMerchantNew),
        list_filter=["merchant_payment_method_id"],
        date_filters=["date_only"],
        default_ordering=["-date_only"],
        fk_fields={"merchant_payment_method_id": "merchant-payment-methods"},
    )
)

# ---------------------------------------------------------------------------
# sandbox
# ---------------------------------------------------------------------------

register(
    AdminModelConfig(
        key="test-credits",
        app="sandbox",
        model=TestCredits,
        verbose_name="Тестовый реквизит",
        verbose_name_plural="Тестовые реквизиты",
        list_display=_all_fields(TestCredits),
        list_filter=["is_active", "wanted_status_callback", "payment_method_id"],
        search_fields=["requisite"],
        editable_fields=["payment_method_id", "requisite", "wanted_status_callback", "is_active", "requisite_details"],
        creatable_fields=[
            "payment_method_id",
            "requisite",
            "wanted_status_callback",
            "is_active",
            "requisite_details",
        ],
        creatable=True,
        deletable=True,
        fk_fields={"payment_method_id": "payment-methods"},
    )
)


def config_to_dict(config: AdminModelConfig) -> dict[str, Any]:
    return {
        "key": config.key,
        "app": config.app,
        "app_label": config.app_label,
        "verbose_name": config.verbose_name,
        "verbose_name_plural": config.verbose_name_plural,
        "list_display": config.list_display,
        "list_filter": config.list_filter,
        "search_fields": config.search_fields,
        "default_ordering": config.default_ordering,
        "editable_fields": config.editable_fields,
        "creatable_fields": config.creatable_fields,
        "creatable": config.creatable,
        "deletable": config.deletable,
        "is_writable": config.is_writable,
        "str_template": config.str_template,
        "fk_fields": config.fk_fields,
        "field_labels": config.field_labels,
        "list_editable": config.list_editable,
        "actions": [{"key": k, "label": label} for k, label in config.actions],
        "hidden": config.hidden,
        "list_per_page": config.list_per_page,
        "field_kinds": field_kinds(config),
    }


def mask_row(config: AdminModelConfig, row: dict[str, Any], role: str | None) -> dict[str, Any]:
    """Masks `config.sensitive_fields` unless `role` may see them. `role=None`
    always masks (used for audit trails, which must never hold secrets)."""
    if not config.sensitive_fields:
        return row
    if role is not None and role_at_least(role, SENSITIVE_MIN_ROLE):
        return row
    return {k: (SENSITIVE_MASK if k in config.sensitive_fields and v else v) for k, v in row.items()}


def field_kinds(config: AdminModelConfig) -> dict[str, str]:
    """Input kind for every editable/creatable field — `bool`, `int`, `decimal`,
    `datetime`, `json` or `text` — so forms render checkboxes and number inputs
    instead of text boxes for everything."""
    from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, Numeric

    kinds: dict[str, str] = {}
    for name in {*config.editable_fields, *config.creatable_fields, *config.list_editable}:
        column = getattr(config.model, name, None)
        col_type = getattr(column, "type", None)
        if isinstance(col_type, Boolean):
            kinds[name] = "bool"
        elif isinstance(col_type, Integer):
            kinds[name] = "int"
        elif isinstance(col_type, Numeric):
            kinds[name] = "decimal"
        elif isinstance(col_type, (DateTime, Date)):
            kinds[name] = "datetime"
        elif isinstance(col_type, JSON):
            kinds[name] = "json"
        else:
            kinds[name] = "text"
    return kinds
