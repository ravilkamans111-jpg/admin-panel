# AmPay Django Monolith — Admin-Panel Migration Research Report

Repo root analyzed: `.../scratchpad/monoliths/ampay/AmPay`
Purpose: inventory everything needed to reimplement the **Django admin surface only** as a standalone FastAPI multi-tenant admin microservice (AmPay / RajaPay / quiet-forest share this codebase, one DB per brand, brand selected via `brand_id` in a JWT).

---

## 1. Project layout — Django apps

| App | Purpose |
|---|---|
| `api_client` | AmPay's own first-party API: in-house payment windows (P2P, India H2H, Mostbet integration), `Currency`/`Bank`/`Card`/`SellingInfo` (conversion-rate) models, USDT rate aggregation (Binance/Bybit/Rapira), Celery tasks that refresh rates and selling info. |
| `api_mediator` | The largest app by file count — one subpackage per external payment-partner integration (~80 partners: aggrepay, airpay, alikassa, asterisk, auris, axepays, bridgepay, clearix, f2pay, finaxis, finvia, jmit, macpay, manypay, nirvana, octopartners, payok, payporto, pelican, platinacash, protocol, smilepayz, solvex, trustgate, etc. — each with a `*_service.py` and `*_callback.py`). Also owns the core commercial data model: `Company` (partner), `PaymentMethod`, `PaymentMethodCompany`, `MerchantPaymentMethod`, `CompanyBalance`, `PaymentMethodCascade`(+Item), `PaymentMethodTemplate`, conversion-caching, and Celery tasks (daily-limit resets, balance alerts, anti-fraud export to n8n). |
| `personal_account_auth` | Merchant identity/auth domain: `Merchant`, `MerchantBalance`, `WhiteList` (IP allow-list), `InviteToken`, `UserConfig` (per-user USDT-exchanger settings). Wraps Django's built-in `auth.User`. |
| `personal_account_transaction` | Core transaction ledger: `Transaction`, `Settlements`, `Appeal` (chargeback/dispute), `AntiFraudBlockedMerchantUsers`. Heavy business logic embedded in model `save()` methods and `business_logic/services/`. |
| `conversion_statistic` | Daily rollup/analytics models: `ConversionStatisticsNew` (by payment method), `ConversionStatisticsPartnersNew` (by partner), `ConversionStatisticsMerchantNew` (by merchant). Read/export-only in admin. |
| `sandbox` | Test-mode payment simulation: `TestCredits` model (fake requisites that trigger deterministic callback outcomes for QA/integration testing). |
| `lk` ("личный кабинет" = merchant personal account) | Non-admin, merchant-facing dashboard backend (views/services for the merchant frontend). No `admin.py`; out of scope for the admin migration but shares services (e.g. `lk/service/settlement_service_lk.py`) with `personal_account_transaction`. |
| `core` | Django project config: `settings.py`, `vault_loader.py` (secrets), `urls.py`, `celery.py`, `asgi.py`/`wsgi.py`, logging config (yaml, dev/prod). |

Non-Django-admin apps/dirs of note: `conf/` (nginx configs), `docs/`, `tests/`, `sandbox` scripts, `utils.py` (shared formatting helpers e.g. `format_decimal`, `Reform`).

---

## 2. Models registered in Django admin

### api_client
- **Currency** — `iso_code` (CharField(3), unique, choice-constrained by `iso4217` library), `addition_name`, `limit` (PositiveInteger), `binance_bank` (FK→Bank, SET_NULL). Table `currency`.
- **Bank** — `name` (unique). Table `bank`.
- **Card** — `name`, `owner_name`, `requisite`, `currency` (FK→Currency), `bank` (FK→Bank), transaction count/amount limits (current + max, both directions), `is_enabled`. Table `card`.
- **SellingInfo** — conversion-rate cache: `currency_for_sale` (FK→Currency), `currency_for_buy` (CharField, default `'USDTTRC'`), `coefficient` (Decimal 14,6), `date_create`, `date_update` (auto_now). `unique_together(currency_for_sale, currency_for_buy)`. Table `selling_info`.

### personal_account_auth
- **Merchant** — `user` (FK→`AUTH_USER_MODEL`, `get_user_model()`), `name`, `public_key` (64-char hex secret, auto-generated in `save()`), `private_key` (128-char hex secret, auto-generated), `transaction_id`, `project_url`. Custom `save()` auto-generates API keys via `secrets.token_hex`; custom `clean()` enforces unique (user, name). `unique_together(user, name)`. **Unusual:** public/private keys are plaintext-stored, generated Python secrets — not hashed/encrypted at rest.
- **MerchantBalance** — `merchant` (FK), `currency` (FK→api_client.Currency), `balance`, `blocked_balance_in/out`, `settlement_commission`, `blocked_balance_usdt_in/out`, `balance_usdt`, `insurance_balance_usdt`. `unique_together(merchant, currency)`.
- **WhiteList** — `merchant` (FK), `allowed_ip` (unique CharField — IP allow-listing per merchant).
- **InviteToken** — `client_name` (unique), `token` (auto hex via `secrets.token_hex(16)`), `used` (bool), `invited_user` (O2O→User, SET_NULL).
- **UserConfig** — O2O→User, `enable_usdt_exchanger`, `exchanger_source`/`pinned_exchange` (TextChoices), stack-page/row config for Binance P2P & Bybit scraping, **`manual_usdt_rates` JSONField** (default `{}`) holding manual per-ISO overrides. Custom `clean()` cross-field validation, custom `save()` normalizes `None`→`{}`.

### api_mediator
- **Company** — partner/company, `name` (unique). (Comment in code: "specifically removed registration so no one accidentally changes something" — historical, but it IS registered via `@admin.register(Company)`.)
- **PaymentMethod** — `name`, `sub_method`, `currency` (FK), `direction` (IN/OUT choice), `token` (auto-generated via `create_token()` in `save()` if blank). `unique_together` on (name,currency,sub_method,direction) and (token,direction).
- **PaymentMethodCompany** — join of PaymentMethod×Company with per-partner economics: `is_active`, `partner_rate`, `additional_commission`, `settlement_commission`, daily amount/count limits, transaction min/max, running daily counters (`current_daily_*`), `last_reset` (auto_now), `changing_rate` flag, `priority` (cascade ordering). Custom `save()` diffs old vs new and calls `invalidate_cache()` (Redis) on business-relevant field changes.
- **MerchantPaymentMethod** — join of Merchant×PaymentMethod: `personal_rate`, `additional_commission`, `test_mode`, `block`, `no_callback`, transaction limits, `only_admin_configure`, `cascade` (FK→PaymentMethodCascade, SET_NULL). Custom `clean()` validates cascade↔method consistency; `save()` triggers cache invalidation.
- **CompanyBalance** — per-company per-currency ledger: `available_balance`, `blocked_balance_in/out`, `our_income`, `clients_funds`, `settlement_commission`, `alert_balance_percent` (0–100, validated), `insurance_balance`.
- **CompanyMethodStatistics** — daily per-partner-method counters (registered admin class exists but **not decorated with `@admin.register`** — effectively dead/unregistered in current code, worth flagging).
- **FloatedProcentsCompanies** — tiered/floating-rate table: `from_amount`/`to_amount`/`rate` per `PaymentMethodCompany`.
- **PaymentMethodTemplate** + **TemplateMethodMapping** — reusable bundles of payment methods with default rate/limits, applied to merchants via bulk admin actions.
- **PaymentMethodCascade** + **PaymentMethodCascadeItem** — ordered fallback list of `PaymentMethodCompany` entries per `PaymentMethod`, with per-item `priority`/`is_active`; used by the routing engine to pick a partner.

### personal_account_transaction
- **Transaction** — the core ledger row: `tracker_id`, `partner_system_id`, `merchant_system_id`, `merchant_client_id` (all UUID-ish string identifiers), `merchant` (FK), `payment_method_company` (FK), `status` (ACCEPTED/SUCCESS/DECLINED/APPEAL), `direction` (IN/OUT), `amount`, `commission`, `partner_income`, `pure_our_income`, `amount_after_commission`, optional `p2p_card`, `status_addition_info`, `addition_info`, `original_tracker_id`, `usdt_fixed_course`, `amount_after_commission_in_usdt`. `unique_together(merchant_system_id, merchant)`. **Unusual:** `save()` branches on a `from_admin=True` kwarg — when saved from the admin it auto-fills missing UUID fields and calls `TransactionSaveService.pre_save_from_admin_transaction()`, which contains real ledger side effects (balance mutation) — i.e. admin edits are NOT simple CRUD, they run business logic.
- **AntiFraudBlockedMerchantUsers** — merchant-scoped user ban list: `second_chance`/`second_chance_counter`/`second_chance_date`, `ban_date`, `permanent_ban`. `save()` also branches on `from_admin` and mutates ban timestamps based on field diffs.
- **Appeal** (dispute/chargeback) — O2O→Transaction, `status`, `description`, `type_appeal`, `photo` (FileField).
- **Settlements** — inter-balance transfer/settlement record: `settl_type` (FROM_PARTNER/TO_PARTNER/TO_MERCHANT/FROM_MERCHANT), `balance_partner` (FK→CompanyBalance), `balance_merchant` (FK→MerchantBalance), `amount`, `commission`, `our_funds`, `clients_funds`, `conversion_rate`, `amount_in_usdt`, `final_amount(_in_usdt)`, `wallet`, `tracker_link`, `transaction_id` (loosely-linked string, not FK), `tg_id`. **`save()` is the most business-logic-heavy method in the codebase** — it calls `SettlementSaveService` which computes commission/funds splits, creates or mutates a companion `Transaction` row, and can raise if consistency checks fail. This MUST be reimplemented explicitly, not treated as ORM plumbing.

### conversion_statistic
- **ConversionStatisticsNew**, **ConversionStatisticsPartnersNew**, **ConversionStatisticsMerchantNew** — near-identical daily rollups (by method / by partner-method / by merchant-method) with `num_requests`, `num_requests_success`, `num_paid_orders`, corresponding `amount_*`, `conversion_percent(_paid_orders)` computed in `save()`, `date_only` (derived from `date_create` in local tz). All read-only in admin (`readonly_fields` = everything except nothing editable).

### sandbox
- **TestCredits** — `payment_method` (FK), `requisite`, `wanted_status_callback` (StatusChoices), `is_active`, `requisite_details` (JSONField, default `dict`). Used to simulate partner callbacks in test mode.

---

## 3. Admin customizations (per registered ModelAdmin)

**api_client**
- `CurrencyAdmin`: list_display(iso_code, limit, binance_bank), search_fields incl. related `binance_bank__name`, custom `form=CurrencyForm`.
- `CardAdmin`: list_filter/list_editable on `is_enabled`, search across related fields.
- `BankAdmin`: plain.
- `SellingInfoAdmin`: custom display method `formatted_date_update` (localizes/formats `date_update`).

**personal_account_auth**
- `MerchantAdmin`: exposes `public_key`/`private_key` directly in `list_display` and `fields` (secrets shown in plaintext in the admin list — a real disclosure risk to carry into the new admin's RBAC design). Two custom bulk **actions**:
  - `apply_template_to_merchants` — intermediate confirmation page (`select_template.html`), bulk-creates `MerchantPaymentMethod` rows from a `PaymentMethodTemplate`'s mappings, skipping existing pairs, wrapped in `transaction.atomic()`.
  - `apply_selected_methods_to_merchants` — dynamic form (`PaymentMethodSelectionForm`) whose checkbox fields are generated at runtime from `PaymentMethod.objects.filter(currency=..., direction=...)`; bulk-creates `MerchantPaymentMethod` rows for selected methods/merchants.
- `MerchantBalanceAdmin`: custom `change_list_template`; **custom admin URL via `get_urls()`** — `refresh-balances/` view (`admin_view`-wrapped) that runs a raw SQL `UPDATE ... FROM (subquery aggregating personal_account_transaction.Transaction)` recalculating `balance`/`blocked_balance_in`/`blocked_balance_out` per merchant/currency directly in Postgres, restricted to `request.user.is_superuser`. Several computed display columns (USDT conversions) using `Prefetch` optimization against `SellingInfo`.
- `WhiteListAdmin`, `InviteTokenAdmin`: standard CRUD with filters.
- `UserConfigAdmin`: fieldsets grouping USDT-exchanger config.

**api_mediator**
- Module-level standalone `@admin.action` functions (not bound to one admin class) for **cache management**, wired into multiple ModelAdmins: `clear_cache_by_currency`, `clear_cache_by_merchant`, `clear_cache_by_method_currency`, `clear_all_payment_methods_cache` — all call `invalidate_cache()` (Redis key deletion via `cache.keys(pattern)` + `delete_many`, django-redis specific).
- `PaymentMethodAdmin`: plain CRUD + filters.
- `CompanyAdmin`: plain (despite a stale code comment claiming it's deliberately unregistered).
- `PaymentMethodCompanyAdmin`: large `list_editable` (nearly the whole commercial config editable inline from the list view — rates, limits, priority, `is_active`); custom `get_search_results()` special-cases an autocomplete GET param `cascade_payment_method_id` and a referer-URL regex hack to filter search results when adding a cascade item from the cascade-edit page; custom `autocomplete_view` override (currently a no-op passthrough); actions: clear-cache-by-currency, clear-all-cache.
- `MerchantPaymentMethodAdmin`: large `list_editable`; `autocomplete_fields`; actions: clear-cache-by-merchant, clear-all-cache.
- `CompanyBalanceAdmin`: computed USDT-conversion display columns (mirrors MerchantBalanceAdmin pattern) via `Prefetch`.
- `CompanyMethodStatisticsAdmin`: defined but **not registered** (`@admin.register` missing) — dead code, flag for the migration (don't reimplement unless product wants it back).
- `FloatedProcentsCompaniesAdmin`: plain.
- `PaymentMethodTemplateAdmin`: `inlines=[TemplateMethodMappingInline]` (TabularInline), computed `get_methods_count`.
- `PaymentMethodCascadeAdmin`: most complex admin class in the codebase —
  - `get_form()` restricts editable fields.
  - `get_readonly_fields()` locks `payment_method` after creation (cascades can't change their target method).
  - `get_inlines()` hides the item inline entirely on the "add" form (items only make sense once the cascade exists).
  - `response_add()` redirects straight to the change view after creation (UX affordance).
  - `save_formset()` re-validates inline items match the parent's payment method (defense-in-depth alongside formset-level `ValidatedFormSet.clean()` which also checks priority uniqueness).
  - `save_model()` enforces uniqueness of (name, payment_method) beyond the DB constraint, with a friendly error.
  - Custom `PaymentMethodCascadeItemInline.formfield_for_foreignkey()` filters the `payment_method_company` dropdown by inspecting `request.resolver_match.kwargs['object_id']` to scope choices to the current cascade's payment method.
  - Custom `PaymentMethodNameFilter(admin.SimpleListFilter)` — avoids extra queries by pulling distinct method names directly.

**personal_account_transaction**
- `TransactionAdmin` (extends `ImportExportModelAdmin` from `django-import-export`): custom `TransactionResource` (CSV/XLSX export column mapping/order); custom `TransactionAdminForm` injects merchant/partner commission rates as `data-*` HTML attributes for **client-side JS calculation** (`Media.js = admin/js/transaction_changes.js` — business math duplicated in a static JS file, must be located/ported); `list_filter` uses third-party `MultiSelectRelatedFieldListFilter`/`DateRangeFilter`/`DateTimeRangeFilter`; custom `UniqueStatusFilter(SimpleListFilter)`; `get_fields()`/`get_readonly_fields()` swap between raw editable fields (create) and computed display-only fields (edit) for `usdt_fixed_course`/`amount_after_commission_in_usdt`; custom UUID-aware `get_search_results()` (tries tracker_id as UUID first, then merchant/partner system IDs, falling back to default search); `save_model()` fetches the *old* transaction DTO and passes `from_admin=True, old_transaction=...` into `Transaction.save()`, which runs `TransactionSaveService` — **admin edits mutate downstream balances via business logic, not plain field updates**; custom **action** `send_callbacks_to_merchants` — replays outbound webhook callbacks to merchants via `CallbacksService.send_message_to_merchant.delay(...)` (Celery).
- `AntiFraudBlockedMerchantUsersAdmin`: `list_editable` on ban fields; `save_model()` similarly diffs old/new state and passes to model `save(from_admin=True, old_value=...)`.
- `AppealAdmin` (dispute/chargeback view): defined but **not registered** (`@admin.register` missing) — another dead-code flag; has an image-preview method `get_file` (`mark_safe` HTML img tag from `photo.url`).
- `SettlementsAdmin`: `TransactionSettlementAdminForm` dynamically adjusts required-ness of `conversion_rate` and filters `balance_partner`/`balance_merchant` querysets based on the instance; a `DynamicChoiceField`/`merchant_balance_label_for_settlement_admin()` helper labels USDT-exchanger merchants differently in the dropdown; `get_readonly_fields()`/`get_exclude()` branch heavily on `settl_type` (FROM_PARTNER/TO_PARTNER lock different fields than TO_MERCHANT/FROM_MERCHANT); custom `get_search_results()` builds an OR'd `Q()` across all `search_fields` including related lookups; `save_model()` again diffs old/new and delegates the actual settlement math to `SettlementSaveService`; action `send_callbacks_to_tg_user` sends a Telegram notification via `CallbacksService.send_message_to_tg_user()`.
  - **Note**: two identically-named `_is_exchanger_for_settlement()` function definitions exist in this file (the second silently shadows the first) — harmless duplication but worth cleaning up during the port rather than porting twice.

**conversion_statistic**
- Three `ImportExportModelAdmin` subclasses (Method/Partner/Merchant variants), all fully `readonly_fields` (view+export only, no create/edit path exercised in practice), each with a custom `ModelResource` for XLSX/CSV export column naming and a duplicated `get_search_results()` OR-query-across-related-fields helper (same pattern copy-pasted 3×).

**sandbox**
- `TestCreditsAdmin`: `fieldsets`, `list_editable` on `is_active`/`wanted_status_callback`, `select_related` optimization.

No `has_add_permission`/`has_change_permission`/`has_delete_permission` overrides exist anywhere except the single `is_superuser` gate inside `MerchantBalanceAdmin.refresh_balances` (a custom view, not a permission-method override).

---

## 4. Auth & permissions model

- **No custom `AUTH_USER_MODEL`.** `AUTH_USER_MODEL` is never set in `settings.py`; the codebase uses Django's stock `django.contrib.auth.models.User` (imported directly in `personal_account_auth/models.py` and via `get_user_model()` elsewhere — both resolve to the same default model).
- **Merchant is a separate profile model FK'd to `User`**, not a swapped user model. A `User` can own multiple `Merchant` rows (unique per `(user, name)`), each with its own API key pair.
- **Admin site auth = Django's default staff/superuser session auth** (`django.contrib.admin` + `AuthenticationMiddleware` + `SessionMiddleware`). No django-admin permission classes, no Django `Group`/`Permission` usage found anywhere in `admin.py` — access control is effectively binary staff vs. non-staff, with a single ad hoc `is_superuser` check gating the raw-SQL balance-refresh view. There is **no row-level/object-level permission logic** (no per-brand or per-merchant scoping of admin data) — any staff user with admin access sees all merchants/companies/transactions.
- **No multi-tenant/brand model exists in this codebase.** AmPay, RajaPay, and quiet-forest are three *separate deployments* of the same code, each with its own Postgres DB and settings (see `MAGIC_LINK_FRONTEND_URL_AMPAY` vs `_QF` in settings.py, `HOST_AMPAY`/`VAULT_MOUNT` per-brand vault paths, hostnames like `am-pay.su` vs `quiet-forest.su`). There is no `Brand`/`Tenant` FK anywhere in the schema — brand identity today is purely at the deployment/config level, not the data level. **This confirms the new FastAPI service's job**: it must introduce the brand scoping (via JWT `brand_id` → DB connection selection) that doesn't exist today; there's no legacy row-level tenant logic to port, but also nothing to reuse — the routing must be built fresh.
- **API-level auth (non-admin, DRF)**: `rest_framework_simplejwt` (JWT) + `TokenAuthentication` as configured `DEFAULT_AUTHENTICATION_CLASSES`; `djoser` for account/password flows; a home-grown "magic link" mechanism (`MAGIC_LINK_ISSUER_BEARER_TOKEN`, TTL settings) for merchant dashboard SSO — **not used by the Django admin itself**, but worth knowing since JWT already carries app-level identity in this system, which is the same pattern the new admin service will extend with `brand_id`.
- Merchant API auth (distinct from admin) uses the `public_key`/`private_key` pair stored in plaintext on `Merchant`.

---

## 5. Secrets & config management

- **Primary mechanism: HashiCorp Vault**, via `hvac`, in `core/vault_loader.py`. Three KV-v2 secret paths are read at import time: `urls` → `VAULT_URLS`, `settings` → `VAULT_SETTINGS`, `api_keys` → `VAULT_API_KEYS`. Vault address/mount/credentials come from env vars (`VAULT_ADDR`, `VAULT_MOUNT`, `VAULT_USERNAME`, `VAULT_PASSWORD`, `VAULT_TOKEN`). If `VAULT_ADDR` is absent it falls back to `python-dotenv` loading a local `.env` — i.e. **Vault in prod, `.env` file for local dev**, both funneling into the same `VAULT_SETTINGS`/`VAULT_URLS`/`VAULT_API_KEYS` dicts.
- `core/settings.py` does `globals().update(VAULT_API_KEYS/SETTINGS/URLS)` — i.e. **every secret from Vault is dumped into module globals**, then read back out via `VAULT_SETTINGS.get(...)`. This is a wide, ungoverned secret surface (any key present in the Vault path becomes a Django setting name silently).
- Notable secret-like settings and their sourcing (`os.getenv` unless noted as Vault):
  | Setting | Source | Notes |
  |---|---|---|
  | `SECRET_KEY` | `VAULT_SETTINGS.get("SECRET_KEY", "django_secret_key")` | **Hardcoded fallback** `core/settings.py:80` — insecure default if Vault lookup fails. |
  | `POSTGRES_PASSWORD` | Vault, with hardcoded `"postgres"` fallback when running in local/test mode | `core/settings.py:50-52` |
  | `DJANGO_SUPERUSER_PASSWORD` | `VAULT_SETTINGS.get(..., "admin")` | **Hardcoded fallback** `core/settings.py:118`. |
  | `DJANGO_SUPERUSER_USERNAME` | `VAULT_SETTINGS.get("SETVER_USER_AMP", "admin")` | fallback `"admin"`, `core/settings.py:66`. |
  | `ADMIN_URL_KEY` | `VAULT_SETTINGS.get(..., "admin")` | Obfuscates the admin URL path; fallback is the trivially guessable `"admin"`, `core/settings.py:119`. |
  | `ADMIN_PUBLIC_KEY` / `ADMIN_PRIVATE_KEY` | Vault, no fallback (`None`) | `core/settings.py:120-121` |
  | `AGENT_REFERRAL_CALLBACK_SECRET` | Vault, fallback `""` | `core/settings.py:89` |
  | `MAGIC_LINK_ISSUER_BEARER_TOKEN` | Vault, **hardcoded fallback token literal** `"mk_issuer_token_dfnglkdsfjgnldskfgdjf34g59rniegotgjklfdngkfgjdfgnfdlgskjrnoagwekrng"` | `core/settings.py:91` — a real-looking bearer token committed to source as a fallback; treat as compromised and rotate regardless of migration. |
  | Celery broker/result backend URLs | Vault (`CELERY_BROKER_URL_AMPAY`, etc.), redis localhost fallback in debug/test | `core/settings.py:61-78` |
  | DB host/port/name/user | Vault (`HOST_AMPAY`, `DB_PORT`, `POSTGRES_DB`, `POSTGRES_USER`) with env-var overrides | `core/settings.py:46-57` |
  | Redis host(s)/port/db | Vault (`REDIS_HOST_AMPAY`, `REDIS_HOST_TEST_AMPAY`, `REDIS_PORT`, `REDIS_DB`) | `core/settings.py:58-60,81` |
  | Every `api_mediator/business_logic/api_services/*/constants.py` (per-partner API keys, e.g. `CLEARIX_API_KEY`, `CLEARIX_NATIVE_API_KEY` referenced in `api_mediator/tasks.py`) | Presumed sourced from `VAULT_API_KEYS` (globals-injected) — **not individually audited file-by-file in this pass**; recommend a follow-up grep of `api_mediator/business_logic/api_services/*/constants.py` before migration to enumerate every partner credential name. |
  | `MERCHANT_TIMEOUTS_MIN` | Hardcoded dict literal in settings.py (`core/settings.py:544-547`) mapping a specific merchant hash to a timeout — not a secret, but an example of business config living in `settings.py` rather than the DB. |
- **Debug prints of internal state**: `core/settings.py:525-535` prints `DEBUG`, `DB_HOST`, `DB_HOST_TEST`, `POSTGRES_PASSWORD`, and Vault lookup results to stdout/logs unconditionally on every process start (not gated by `DEBUG`) — this leaks the DB password into logs in every environment including production. Flag explicitly for anyone touching this file, though it's out of scope to fix here since this is read-only research.
- `core/settings.py:537-538` scrubs the *env-var* copies of a handful of keys after use (`os.environ.pop`) — cosmetic only, since the values are already captured into Python globals/Vault dicts by that point.
- **Recommendation for the FastAPI service**: do not replicate the "dump Vault dict into globals" pattern — enumerate exactly the secrets needed (DB creds per brand, JWT signing key, any admin-action-triggered partner credentials) and load them explicitly per-brand via Vault paths keyed by `brand_id`.

---

## 6. Database

- **Engine**: PostgreSQL only — `"ENGINE": "django.db.backends.postgresql_psycopg2"` (`core/settings.py:382`), single `default` DB in `DATABASES`, **no database routers, no multi-db config** in this codebase (each brand is a fully separate deployment/DB, not a Django multi-db setup within one process).
- Host/port switch on `DEBUG`: uses `DB_HOST_TEST`/test port when `DEBUG` is true, else `DB_HOST`/prod.
- `DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"` — all PKs are 64-bit auto-increment integers (SQLAlchemy equivalent: `BigInteger` PK, `autoincrement=True`).
- ORM field types worth carrying into a SQLAlchemy model (patterns repeated across the schema):
  - Money/amount fields are **all `DecimalField`**, precisions vary by domain: transaction amounts `max_digits=10, decimal_places=2`; balances `max_digits=18, decimal_places=2`; USDT-denominated fields `max_digits=20, decimal_places=8` (crypto precision); percentages/rates `max_digits=5, decimal_places=2`. **Use `Numeric(precision, scale)` in SQLAlchemy, never `Float`, matching each field's exact precision** — several places do decimal division (`balance / conversion_coefficient`) so precision loss would be a real financial bug.
  - `JSONField` used in `UserConfig.manual_usdt_rates` (manual per-currency USDT rate overrides) and `TestCredits.requisite_details`. Map to `sqlalchemy.JSON`/`JSONB`.
  - `unique_together` is used extensively (composite uniqueness) rather than single unique constraints — must become composite `UniqueConstraint`s.
  - Several FKs use `on_delete=SET_NULL` with `null=True` (soft-decoupling, e.g. `Card.bank`, `InviteToken.invited_user`, `MerchantPaymentMethod.cascade`) vs `CASCADE` elsewhere (hard-delete propagation, e.g. most Merchant/Company relations) — preserve delete semantics per-FK when porting, they encode real business rules about what can outlive what.
  - `auto_now_add` / `auto_now` timestamp pairs (`date_create`/`date_update`) are the standard audit-timestamp convention across nearly every model — replicate as server-default `now()` + `onupdate=now()`.
  - `Settlements.transaction_id` and a couple of similar fields are **string-typed loose references to another table's business key, not real FKs** — i.e. referential integrity for that link is enforced only in application code (`SettlementSaveService`), not the DB. This must be explicitly reimplemented in the new service, not assumed.
  - `db_table` is explicitly set on every model (snake_case, often singular) — preserve these exact table names if the FastAPI service reads the same physical tables rather than migrating data.

---

## 7. Domain models relevant to a payments admin

- **Transactions** (`personal_account_transaction.Transaction`): IN/OUT ledger entries, status machine `ACCEPTED → SUCCESS | DECLINED | APPEAL`. Admin exposes manual status editing (with heavy `TransactionSaveService` side effects on save — balance mutation), CSV/XLSX export, and a **"resend callback to merchant" action** (queues `CallbacksService.send_message_to_merchant.delay(id)` — a Celery task that POSTs a signed callback to the merchant's webhook URL).
- **Merchants** (`personal_account_auth.Merchant`): the tenant-equivalent-within-a-brand entity; owns API keys, balances, whitelisted IPs, payment-method subscriptions. Admin actions: bulk-apply a `PaymentMethodTemplate` or an ad hoc set of `PaymentMethod`s to selected merchants (creates `MerchantPaymentMethod` rows).
- **Wallets/balances**: `MerchantBalance` (per merchant×currency) and `CompanyBalance` (per partner×currency) — admin exposes a raw-SQL **"refresh balances" recompute action** (superuser-only) that recalculates balances from the `Transaction` table, and read-only USDT-equivalent display columns computed from `SellingInfo` conversion rates.
- **Payouts/Partners**: `Company` (partner/PSP), `PaymentMethod`, `PaymentMethodCompany` (partner-specific rate/limit config), `PaymentMethodCascade`/`Item` (fallback routing chain across partners for a given method) — this cascade IS the payout/routing engine's config surface, fully admin-editable.
- **KYC**: no dedicated KYC model found in this codebase (not implemented here, or lives in `lk`'s merchant-facing flow rather than admin — `lk/` has no `admin.py`).
- **API keys**: `Merchant.public_key`/`private_key` (plaintext, auto-generated, admin-visible in list view — a candidate for hashing/masking in the new service).
- **Webhooks**: outbound merchant callbacks are triggered from admin (`send_callbacks_to_merchants` action) and from Celery tasks (`execute_after_timeout`, `send_callback_to_sandbox`); inbound partner webhooks land in each `api_mediator/business_logic/api_services/<partner>/*_callback.py` (not admin-facing, but the state changes they cause — status transitions — ARE what the admin edits/overrides).
- **Invoices**: no distinct invoice model; `Transaction` IS the invoice/payment-intent equivalent.
- **Disputes/chargebacks**: `Appeal` model (transaction ↔ appeal 1:1, status/description/type/photo) — admin class exists (`AppealAdmin`) but is **not registered** (`@admin.register` missing), so appeals are currently only manageable via direct DB/shell access, not the live admin UI. Confirm with the team whether this is intentional before deciding whether to build it into the new service.
- **Settlements** (inter-party fund movement: partner↔platform↔merchant): `Settlements` model, four types (`FROM_PARTNER`, `TO_PARTNER`, `TO_MERCHANT`, `FROM_MERCHANT`), the most business-logic-dense admin form in the app — dynamically required/hidden fields depending on type, and a `save()` that both updates balances and creates/mutates a companion `Transaction` row.

---

## 8. Background jobs / Celery

Celery configured via `core/celery.py`/`CELERY_BEAT_SCHEDULE` in settings (Redis broker+backend, per-brand URLs from Vault). Beat schedule (not admin-triggered, but relevant context):
- `api_client.tasks.form_selling_info_for_available_fiats` — every 15 min (comment says should be 5 in prod).
- `api_mediator.tasks.update_payment_methods_company_daily_limits` — daily at midnight.
- `api_mediator.tasks.check_company_balances_alerts` — every 30 min (insurance-balance threshold alerting).
- `api_client.tasks.update_usdt_rates` / `update_usdt_rates_for_exchanger_users` — every 30s.
- `api_mediator.tasks.export_india_spammers_to_n8n` (hourly) / `export_daily_india_spammers_to_n8n` (daily 09:00) — anti-fraud export to an n8n webhook.

**Tasks triggered directly from admin actions** (the ones that matter for the FastAPI reimplementation, since the new service will need either to call these same Celery tasks or reimplement their side effects synchronously/via its own job queue):
- `CallbacksService.send_message_to_merchant.delay(transaction.id)` — from `TransactionAdmin.send_callbacks_to_merchants` action.
- `CallbacksService.send_message_to_tg_user(settlement.id)` — from `SettlementsAdmin.send_callbacks_to_tg_user` action (note: called directly, not via `.delay()`, in the code as read — verify whether `send_message_to_tg_user` is itself a `@shared_task` with its own dispatch, since the call site doesn't use `.delay()`).
- Other tasks (`execute_after_timeout`, `send_callback_to_sandbox`) are scheduled from transaction-creation flow, not from admin, but are part of the same status-transition machinery an admin status edit interacts with.

---

## 9. Dependencies (admin/auth/DRF/secrets-relevant, from `requirements.txt` / `pyproject.toml`)

| Package | Version | Role |
|---|---|---|
| Django | 4.2.3 | Core framework + admin site |
| djangorestframework | 3.14.0 | DRF (non-admin API) |
| djangorestframework-simplejwt | 5.2.2 | JWT auth for API/dashboard |
| djoser | 2.2.3 | Auth endpoints (register/reset/etc.) |
| drf-spectacular | 0.27.0 | OpenAPI schema/docs |
| django-import-export | 3.3.3 | Admin CSV/XLSX import-export (`ImportExportModelAdmin`, used by Transaction & all 3 conversion-statistic admins) |
| django-admin-rangefilter | 0.11.2 | Date/datetime range admin filters |
| django-admin-multi-select-filter | 1.3.0 | Multi-select admin filters (`MultiSelectFieldListFilter`, `MultiSelectRelatedFieldListFilter`) |
| django-more-admin-filters | 1.7 | Listed in `INSTALLED_APPS`, additional filter widgets |
| django-select2 | 8.1.2 | Select2 widgets in admin forms |
| django-redis | 5.4.0 | Cache backend used for payment-method cache invalidation actions |
| django-cors-headers | 4.3.1 | CORS for the merchant-facing frontend |
| django-otp | 1.2.2 | Present in requirements but **not found wired into `INSTALLED_APPS`/settings** — likely unused/legacy dependency; verify before assuming 2FA exists. |
| social-auth-app-django / social-auth-core | 5.4.1 / 4.5.4 | Present in requirements but **no usage found** in settings/urls in this pass — likely legacy/unused. |
| hvac | 2.3.0 | HashiCorp Vault client (secrets) |
| python-dotenv | 1.0.0 | `.env` fallback for local/dev secrets |
| celery / kombu / amqp / billiard / vine | 5.3.6 / etc. | Background job queue |
| psycopg2-binary | 2.9.9 | Postgres driver |
| iso4217 | 1.11.20220401 | Currency ISO-code choices source for `Currency.iso_code` |
| django-health-check | 3.20.0 | Health endpoints (`INSTALLED_APPS` entries commented out in settings.py despite being installed — health checks are currently disabled, only `v2/ht/` URL include is live, worth double-checking at runtime) |

---

## 10. Other architecturally notable points for the FastAPI rebuild

1. **Business logic lives inside Django model `save()` methods, not just in admin.py.** `Transaction.save()`, `Settlements.save()`, and `AntiFraudBlockedMerchantUsers.save()` all branch on a `from_admin` kwarg and delegate to `business_logic/services/*_save.py` service classes that mutate related balances. Porting the admin UI alone is insufficient — these service methods (`TransactionSaveService`, `SettlementSaveService`, `BlockListService`) are the real business logic and must be ported explicitly, with the same old-value-diffing behavior the admin currently relies on (each `ModelAdmin.save_model()` fetches the pre-edit DTO and passes it in — this "diff old vs new to decide side effects" pattern needs an equivalent in FastAPI, e.g. read-before-write in the endpoint handler).
2. **Redis cache invalidation is tightly coupled to admin saves/actions.** `PaymentMethodCompany.save()` and `MerchantPaymentMethod.save()` call `invalidate_cache()` directly from the model layer (not just from admin actions), using django-redis-specific `cache.keys(pattern)` + `delete_many` (pattern-based deletion — won't directly translate to a generic Redis client without re-implementing key-scanning). Multiple *manual* admin actions also exist to force cache resets (per-currency, per-merchant, per-method-currency, full-flush) — these need first-class equivalents in the new admin (support staff use them operationally).
3. **A raw SQL admin action exists** (`MerchantBalanceAdmin.refresh_balances`) that recomputes balances by aggregating the `Transaction` table directly in Postgres — this bypasses the ORM/service layer entirely and is superuser-gated. This kind of "escape hatch" operational tool should be consciously redesigned (as an explicit reconciliation endpoint) rather than silently dropped, since ops likely relies on it.
4. **Client-side JS carries business math**: `TransactionAdmin`/`SettlementsAdmin` attach custom JS (`admin/js/transaction_changes.js`, `admin/js/transaction_settlement.js`, not read in this pass — recommend explicitly reading them before the port) via Django admin's `Media` class, and the `TransactionAdminForm`/`TransactionSettlementAdminForm` inject computed values as `data-*` HTML attributes for that JS to consume — i.e. some commission/limit calculations shown live in the admin UI happen in JavaScript, not the backend. The FastAPI+SPA equivalent needs its own frontend calc layer or should move this logic server-side.
5. **`post_migrate` signal handlers seed data** (`api_client.apps.on_migrate` populates default `Bank`/`Currency`/`SellingInfo`; `api_mediator.apps.on_migrate` populates default `Company`/`PaymentMethod`/`PaymentMethodCompany`) — these are effectively database seed scripts tied to Django's migration lifecycle; the new service will need its own seed/bootstrap mechanism per brand DB, decoupled from Django.
6. **Dynamic/runtime-generated admin forms**: `PaymentMethodSelectionForm` in `personal_account_auth/admin.py` builds form fields dynamically at `__init__` time based on a queryset filtered by submitted `currency`/`direction` — a pattern (conditional/dependent form fields) that needs a deliberate UI+API design in a SPA/FastAPI world (can't rely on Django's server-rendered form re-init).
7. **Field-level required-ness and visibility depend on instance state** throughout `SettlementsAdmin` (`get_readonly_fields`, `get_exclude`, form `__init__`/`clean` all branch on `settl_type` and on whether the counterparty merchant is flagged as a USDT "exchanger" via `UserConfig.enable_usdt_exchanger`). This state-dependent field behavior is exactly the kind of thing that's easy to under-specify when rewriting from admin.py into an API+SPA — recommend enumerating all `settl_type` × exchanger-flag combinations as an explicit table/spec before building the new settlement endpoint.
8. **No Django signals are used for the core business flows** (searched for `post_save`/`pre_save`/`@receiver`); the only signal usage is `post_migrate` (data seeding, above) plus scattered per-partner integration modules under `api_mediator/business_logic/api_services/*/` (not admin-related — likely internal event dispatch within those partner integrations, not audited in depth here).
9. **Two custom middleware classes** (`api_client.presentation.middleware.AjaxMiddleware`, `RequestMiddleware`) sit in the request pipeline — not admin-specific as far as this pass found, but worth a quick read if any admin views are found to depend on request-scoped state they inject.
10. **Duplicate/dead code hygiene issues** worth flagging to the team (not fixed, per read-only scope): `CompanyMethodStatisticsAdmin` and `AppealAdmin` are defined but never `@admin.register`'d (dead in the live admin); `_is_exchanger_for_settlement()` is defined twice verbatim in `personal_account_transaction/admin.py`; the `CompanyAdmin` docstring/comment claims deliberate non-registration while the class is in fact registered. None of these affect what must be migrated, but they're useful signals about which admin surfaces are actually used in production vs. vestigial.
11. **Third-party admin filter libraries are used extensively** (`rangefilter`, `django_admin_multi_select_filter`, custom `SimpleListFilter` subclasses like `UniqueStatusFilter`, `PaymentMethodNameFilter`) — the FastAPI admin's filtering/query API needs to reproduce this filter vocabulary (date ranges, multi-select on FK/choice fields, distinct-value-derived filters) since staff workflows likely depend on it.
