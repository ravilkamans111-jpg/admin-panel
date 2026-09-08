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

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import DeclarativeBase

from app.models.tenant import (
    AntiFraudBlockedMerchantUsers,
    Appeal,
    Bank,
    Card,
    Company,
    CompanyBalance,
    CompanyMethodStatistics,
    ConversionStatisticsMerchantNew,
    ConversionStatisticsNew,
    ConversionStatisticsPartnersNew,
    Currency,
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
    UserConfig,
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
    notes: str | None = None
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
        list_display=_all_fields(Transaction),
        list_filter=["status", "direction", "merchant_id", "payment_method_company_id"],
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
    )
)

register(
    AdminModelConfig(
        key="settlements",
        app="personal_account_transaction",
        model=Settlements,
        verbose_name="Сеттлмент",
        verbose_name_plural="Сеттлменты",
        list_display=_all_fields(Settlements),
        list_filter=["status", "settl_type"],
        search_fields=["transaction_id", "wallet", "tracker_link", "tg_id"],
        default_ordering=["-date_create"],
        notes=(
            "Создание/правка сеттлмента создаёт или обновляет связанную "
            "транзакцию как часть сохранения (SettlementSaveService) — "
            "перенесено в app.services.settlement_write_service."
        ),
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
    )
)

register(
    AdminModelConfig(
        key="appeals",
        app="personal_account_transaction",
        model=Appeal,
        verbose_name="Апелляция",
        verbose_name_plural="Апелляции",
        list_display=_all_fields(Appeal),
        list_filter=["status", "type_appeal"],
        search_fields=["description", "type_appeal"],
    )
)

register(
    AdminModelConfig(
        key="antifraud-blocks",
        app="personal_account_transaction",
        model=AntiFraudBlockedMerchantUsers,
        verbose_name="Антифрод-блокировка",
        verbose_name_plural="Антифрод-блокировки",
        list_display=_all_fields(AntiFraudBlockedMerchantUsers),
        list_filter=["permanent_ban", "second_chance"],
        search_fields=["merchant_name", "user_id"],
        default_ordering=["-date_create"],
        notes=(
            "Правка запускает ту же diff-логику, что и в оригинале "
            "(save(from_admin=True, old_value=...)) — перенесено в "
            "app.services.antifraud_write_service. Особенность источника, "
            "сохранённая как есть: merchant_name НЕ пересчитывается при "
            "правке через админку, даже если изменить merchant."
        ),
        editable_fields=["merchant_id", "user_id", "second_chance", "permanent_ban"],
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
        notes=(
            "public_key/private_key хранятся в исходной схеме в открытом виде — "
            "показ их здесь несёт реальный риск утечки; перед более широким "
            "раскатыванием стоит ужесточить RBAC на это поле."
        ),
    )
)

register(
    AdminModelConfig(
        key="merchant-balances",
        app="personal_account_auth",
        model=MerchantBalance,
        verbose_name="Баланс мерчанта",
        verbose_name_plural="Балансы мерчантов",
        list_display=_all_fields(MerchantBalance),
        list_filter=["merchant_id", "currency_id"],
        default_ordering=["-id"],
        notes=(
            "«Обновить балансы» (только суперадминистратор) пересчитывает balance/"
            "blocked_balance_in/out из леджера транзакций сырым SQL — см. "
            "app.services.merchant_balance_service. Это UPDATE, не upsert: пары "
            "мерчант/валюта без существующей строки баланса не создаются."
        ),
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
    )
)

register(
    AdminModelConfig(
        key="user-configs",
        app="personal_account_auth",
        model=UserConfig,
        verbose_name="USDT-настройки пользователя",
        verbose_name_plural="USDT-настройки пользователей",
        list_display=_all_fields(UserConfig),
        list_filter=["enable_usdt_exchanger", "exchanger_source"],
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
    )
)

register(
    AdminModelConfig(
        key="payment-method-companies",
        app="api_mediator",
        model=PaymentMethodCompany,
        verbose_name="Конфиг метода у партнёра",
        verbose_name_plural="Конфиги методов у партнёров",
        list_display=_all_fields(PaymentMethodCompany),
        list_filter=["is_active", "company_id", "payment_method_id"],
        default_ordering=["priority"],
        notes=(
            "save() сверяет изменения полей is_active/лимитов/priority и инвалидирует Redis-кэш "
            "методов оплаты (см. app.services.payment_method_write_service) — изменение "
            "partner_rate в одиночку кэш НЕ сбрасывает, это сохранено как в оригинале."
        ),
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
    )
)

register(
    AdminModelConfig(
        key="merchant-payment-methods",
        app="api_mediator",
        model=MerchantPaymentMethod,
        verbose_name="Платёжный метод мерчанта",
        verbose_name_plural="Платёжные методы мерчантов",
        list_display=_all_fields(MerchantPaymentMethod),
        list_filter=["test_mode", "block", "merchant_id", "payment_method_id"],
        notes=(
            "save() безусловно инвалидирует Redis-кэш методов оплаты для мерчанта на КАЖДОЕ "
            "сохранение, независимо от того, какие поля изменились (см. "
            "app.services.payment_method_write_service)."
        ),
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
    )
)

register(
    AdminModelConfig(
        key="company-balances",
        app="api_mediator",
        model=CompanyBalance,
        verbose_name="Баланс компании",
        verbose_name_plural="Балансы компаний",
        list_display=_all_fields(CompanyBalance),
        list_filter=["company_id", "currency_id"],
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
        notes=(
            "Самая сложная бизнес-логика валидации во всей исходной кодовой базе — "
            "см. app.services.cascade_write_service. payment_method неизменяем после "
            "создания (как в оригинале: поле становится readonly при редактировании)."
        ),
        editable_fields=["name", "description", "is_active"],
        creatable_fields=["name", "payment_method_id", "description", "is_active"],
        creatable=True,
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
        notes=(
            "payment_method_company.payment_method должен совпадать с payment_method "
            "каскада; priority уникален в рамках каскада — см. app.services.cascade_write_service."
        ),
        editable_fields=["payment_method_company_id", "priority", "is_active"],
        creatable_fields=["cascade_id", "payment_method_company_id", "priority", "is_active"],
        creatable=True,
        deletable=True,
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
        default_ordering=["-date_only"],
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
        default_ordering=["-date_only"],
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
        default_ordering=["-date_only"],
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
        "notes": config.notes,
        "editable_fields": config.editable_fields,
        "creatable_fields": config.creatable_fields,
        "creatable": config.creatable,
        "deletable": config.deletable,
        "is_writable": config.is_writable,
    }
