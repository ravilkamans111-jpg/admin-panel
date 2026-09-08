# RajaPay — Django Admin-Panel Migration Research Report

Repo root: `.../scratchpad/monoliths/rajapay/rajapay`
Django 4.2.3, Python (typed, `from __future__ import annotations` throughout), Postgres backend, Celery + Redis, deployed behind `v2/` URL prefix.

---

## 1. Project layout (Django apps)

| App | Purpose |
|---|---|
| **api_client** | Currency/Bank/Card reference data + `SellingInfo` (FX conversion coefficients used to price everything in USDT). Also hosts an `AjaxMiddleware`/`RequestMiddleware` used globally, and a Celery-driven USDT rate updater. |
| **api_mediator** | The core payment-gateway integration layer. Owns `Company` (payment partner), `PaymentMethod`, `PaymentMethodCompany` (a partner's config for a method incl. rates/limits/daily counters), `MerchantPaymentMethod` (merchant-specific config), `CompanyBalance`, cascading/priority routing (`PaymentMethodCascade`/`PaymentMethodCascadeItem`), and `business_logic/api_services/<50+ gateway integrations>` (ampay, airpay, alikassa, aggrepay, brusnika, cashonrails, clearix, daxchain, jmit, offsetpay, payport, rizonpay, starpago, trustgate, etc.) — each a bespoke client for a specific upstream payment provider/bank. This is **not** admin logic; it's the payment-processing engine the admin panel configures/monitors. |
| **personal_account_auth** | Merchant/tenant model (`Merchant`, `MerchantBalance`, `WhiteList`, `InviteToken`, `UserConfig`), all keyed off Django's stock `auth.User`. This is the closest thing to a "tenant" concept in the schema (one User → one/many Merchant rows). |
| **personal_account_transaction** | The transaction ledger: `Transaction`, `Settlements`, `Appeal`, `AntiFraudBlockedMerchantUsers`. Very large `admin.py` — this is the operational heart of the admin panel (transaction editing, settlement creation, fraud blocklist). |
| **conversion_statistic** | Rollup/reporting tables: `ConversionStatisticsNew` (per payment method), `...PartnersNew`, `...MerchantNew` — daily conversion-rate stats, admin-exposed as read-only, exportable via `django-import-export`. |
| **lk** ("личный кабинет" = merchant self-service dashboard) | Non-admin, merchant-facing API views (balance, transactions, settlements, SSE for live USDT rates). Not registered in Django admin but shares models/services with the admin surface. |
| **sandbox** | `TestCredits` model — lets ops staff configure fake payment requisites that auto-return a chosen callback status, for merchant integration testing. |
| **core** | Django project settings, Vault-backed secret loading (`vault_loader.py`), Celery app config, URL root, logging config. |
| **tests** | pytest-style tests for callbacks and personal_account_auth (magic-link/ticket auth). |
| **docs** | OpenAPI/styleguide assets (not code). |

Apps with **no** `admin.py` (not part of the admin surface): `api_client` presentation views/business logic beyond its 4 registered models, `api_mediator`'s 50+ gateway clients, `lk`, `personal_account_auth`'s auth business logic (magic links), `conf`.

---

## 2. Models registered in Django admin

### api_client (`api_client/admin.py`, `api_client/models.py`)
- **Currency**: `iso_code` (CharField, `choices=ISO_CODE_CHOICES` built from the `iso4217` package, unique), `addition_name` (Char), `limit` (PositiveInteger), `binance_bank` FK→Bank (nullable, SET_NULL). Custom form (`CurrencyForm`).
- **Bank**: `name` (Char, unique).
- **Card**: `name`, `owner_name`, `requisite` (Char), `currency` FK→Currency (CASCADE), `bank` FK→Bank (nullable, CASCADE), transaction count/amount limits (Decimal/PositiveInteger), `current_transaction_count`/`current_transaction_amount` (running counters), lower/upper per-tx limits, `is_enabled` (Boolean).
- **SellingInfo**: `currency_for_sale` FK→Currency, `currency_for_buy` (Char, default `'USDTTRC'` — not an FK, a free-text coin label), `coefficient` (Decimal 14,6 — the FX rate), `date_create`/`date_update`. `unique_together(currency_for_sale, currency_for_buy)`. This table is the pricing oracle every USDT conversion display in the admin depends on.

### api_mediator (`api_mediator/admin.py`, `api_mediator/models.py`)
- **Company**: `name` (unique) — a payment partner/provider entity.
- **PaymentMethod**: `name`, `sub_method` (nullable), `currency` FK→api_client.Currency, `direction` (`IN`/`OUT` choices), `token` (auto-generated on save via `create_token(iso_code, name, sub_method)` if blank). `unique_together` on (name, currency, sub_method, direction) and (token, direction).
- **PaymentMethodCompany**: `payment_method` FK, `company` FK, `is_active`, `partner_rate` (Decimal 5,2 %), `additional_commission`, `settlement_commission`, `daily_amount_limit`/`daily_count_limit`, `transaction_min_limit`/`max_limit`, running daily counters (`current_daily_amount`, `current_daily_amount_success`, `current_daily_count`, `current_daily_coun_success` [sic typo]), `last_reset` (auto_now), `changing_rate` (Boolean), `priority` (routing priority, lower = higher priority). `save()` diffs cache-relevant fields and calls `invalidate_cache()` (Redis-backed cache invalidation service) — **business logic embedded in the model, triggered by admin edits**.
- **MerchantPaymentMethod**: `merchant` FK→personal_account_auth.Merchant, `payment_method` FK, `personal_rate`, `additional_commission`, `test_mode` (Boolean), `block` (Boolean), `no_callback` (Boolean — suppress merchant callbacks), transaction limits, `only_admin_configure` (Boolean), `cascade` FK→PaymentMethodCascade (nullable). Has `clean()` validating cascade↔method consistency, and `save()` triggers `invalidate_cache(merchant_id=...)`.
- **CompanyBalance**: per-company-per-currency ledger: `available_balance`, `blocked_balance_in/out`, `our_income`, `clients_funds`, `settlement_commission`, `alert_balance_percent` (0-100, validated), `insurance_balance` — used by a Celery alert task (see §8).
- **CompanyMethodStatistics**: daily rollup (`date`, counts, amounts, `conversion_percentage`) per `PaymentMethodCompany`. Registered as a plain class (not `@admin.register`'d — **dead/unregistered admin class**, worth flagging: `CompanyMethodStatisticsAdmin` is defined but never registered via decorator or `admin.site.register()`).
- **FloatedProcentsCompanies**: tiered/floating commission rates (`from_amount`, `to_amount`, `rate`) per `PaymentMethodCompany`.
- **PaymentMethodTemplate** / **TemplateMethodMapping**: reusable bundle of payment-method configs (name, currency, direction, default rate/limits, test_mode) + M2M-like mapping table, used by an admin action to bulk-provision merchants.
- **PaymentMethodCascade** / **PaymentMethodCascadeItem**: ordered routing cascades — a named, ordered list of `PaymentMethodCompany` entries (priority) that the payment engine walks through for a given `PaymentMethod`. Both have `clean()` validators enforcing consistency and uniqueness.

### personal_account_auth (`personal_account_auth/admin.py`, `.../models.py`)
- **Merchant**: `user` FK→`django.contrib.auth.models.User` (stock User, not custom `AUTH_USER_MODEL`), `name` (default `'TEST'`), `public_key`/`private_key` (auto-generated via `secrets.token_hex(32)`/`(64)` on save — **API credentials generated and stored in plaintext in the DB, and shown in the admin `list_display`**), `transaction_id`, `project_url`. `unique_together(user, name)`.
- **MerchantBalance**: `merchant` FK, `currency` FK, `balance`, `blocked_balance_in/out`, `settlement_commission`, plus USDT-denominated shadow fields (`balance_usdt`, `blocked_balance_usdt_in/out`, `insurance_balance_usdt`) added for an "exchanger" merchant type. `unique_together(merchant, currency)`.
- **WhiteList**: `merchant` FK, `allowed_ip` (unique) — IP allow-listing per merchant.
- **InviteToken**: `client_name` (unique), `token` (auto `secrets.token_hex(16)`), `used` (Boolean), `invited_user` O2O→User (nullable, SET_NULL).
- **UserConfig**: O2O→User, `enable_usdt_exchanger` (Boolean), `exchanger_source`/`pinned_exchange` (TextChoices), several nullable "stack page/rows/row_from/row_to" ints controlling how Binance P2P/Bybit order-book depth is sampled, and `manual_usdt_rates` — a **JSONField** (`{"KZT": "520.5", ...}`) letting ops override per-user USDT rates by ISO code. Has custom `clean()` cross-field validation and a `save()` guard that coerces `None`→`{}` for the JSON column (DB column is NOT NULL).

### personal_account_transaction (`personal_account_transaction/admin.py`, `.../models.py`)
- **Transaction**: the ledger row. `tracker_id`, `partner_system_id`, `merchant_system_id`, `merchant_client_id` (all string identifiers, several auto-UUID'd when saved from admin), `merchant` FK, `payment_method_company` FK, `status` (TextChoices: ACCEPTED/SUCCESS/DECLINED/APPEAL, indexed), `direction` (IN/OUT, indexed), `date_create`/`date_update`, `callback_url`, `amount`, `commission`, `partner_income`, `pure_our_income`, `amount_after_commission` (all Decimal 10,2), optional `p2p_card`, `status_addition_info`, `addition_info`, `original_tracker_id`, `usdt_fixed_course` (Decimal 18,8), `amount_after_commission_in_usdt` (Decimal 20,8). `unique_together(merchant_system_id, merchant)`. **`save()` has an `from_admin=True` kwarg path** that auto-fills missing UUID identifiers, computes `amount_after_commission_in_usdt`, builds a `TransactionDTO`, and calls `TransactionSaveService.pre_save_from_admin_transaction(new, old)` — i.e. admin edits to a transaction run through the same business-logic service layer as the API, with balance-adjustment side effects. This is the single most important piece of logic to reimplement faithfully.
- **AntiFraudBlockedMerchantUsers**: `merchant` FK (SET_NULL), `merchant_name` (denormalized), `user_id` (string — merchant's own user identifier, not FK), `second_chance`/`second_chance_counter`/`second_chance_date`, `ban_date`, `permanent_ban`. `save(from_admin=True, old_value=...)` computes ban/second-chance timestamps by diffing against `BlockListDTO`.
- **Appeal**: O2O→Transaction, `status`, `description`, `type_appeal`, `photo` (FileField).
- **Settlements**: `status`, `settl_type` (FROM_PARTNER/TO_MERCHANT/FROM_MERCHANT/TO_PARTNER), `balance_partner` FK→CompanyBalance, `balance_merchant` FK→MerchantBalance, `amount`, `commission`, `our_funds`, `clients_funds`, `conversion_rate`, `amount_in_usdt`, `final_amount`, `final_amount_in_usdt`, `wallet`, `tracker_link`, `transaction_id` (string, not FK — links loosely to `Transaction.tracker_id`), `tg_id`. **`save()` is extremely heavy**: builds `SettlementsDTO`, calls `SettlementSaveService.pre_save_always_settlement` then `pre_save_from_admin_settlement`, and **creates or mutates a `Transaction` row as a side effect of saving a Settlement** (see business_logic/services/settlements_save.py). Any FastAPI reimplementation must replicate this settlement→transaction sync, not just do a straight CRUD write.

### conversion_statistic (`conversion_statistic/admin.py`, `.../models.py`)
Three structurally-identical models — **ConversionStatisticsNew** (per `PaymentMethod`), **ConversionStatisticsPartnersNew** (per `PaymentMethodCompany`), **ConversionStatisticsMerchantNew** (per `MerchantPaymentMethod`) — each with `num_requests`, `num_requests_success`, `num_paid_orders`, `amount_requests`, `amount_requests_success`, `amount_paid_orders`, `conversion_percent`, `conversion_percent_paid_orders`, `date_create`/`data_update`/`date_only`. Each model's `save()` recomputes the two percentage fields and normalizes `None`→`0`/`Decimal(0)` — duplicate logic across all three (candidate for shared mixin, not done in the source). All are read-only in admin (`readonly_fields` = all list fields) and only mutated by background jobs, not admin users directly.

### sandbox (`sandbox/admin.py`, `.../models.py`)
- **TestCredits**: `payment_method` FK, `requisite` (Char), `wanted_status_callback` (StatusChoices, default SUCCESS), `is_active` (Boolean), `requisite_details` (**JSONField**, default `dict`). `unique_together(payment_method, requisite)`. Lets ops define fake test requisites that force a chosen callback outcome for merchant sandbox testing.

---

## 3. Admin customizations (per registered ModelAdmin)

**api_client**
- `CurrencyAdmin`: list_display (iso_code, limit, binance_bank), search_fields incl. related `binance_bank__name`, custom form.
- `CardAdmin`: list_filter (`bank__name`, `is_enabled`), `list_editable=('is_enabled',)`, list_per_page=20.
- `BankAdmin`: filter/search by name.
- `SellingInfoAdmin`: custom `formatted_date_update` display method (localtime formatting).

**api_mediator**
- Four module-level `@admin.action` functions used across multiple ModelAdmins for **cache management**: `clear_cache_by_currency`, `clear_cache_by_merchant`, `clear_cache_by_method_currency` (defined but unused — not attached to any `actions=[...]`), `clear_all_payment_methods_cache` — all call into `personal_account_transaction...cache_invalidation.invalidate_cache()` (Redis-backed method-lookup cache).
- `PaymentMethodAdmin`: standard list/filter/search.
- `CompanyAdmin`: comment says "specially removed registration so no one accidentally changes something" but it **is** registered — comment is stale/misleading, not enforced by code (no permission override).
- `PaymentMethodCompanyAdmin`: large `list_editable` (rates/limits directly editable inline in the changelist — high blast-radius UI), custom `get_queryset` (select_related optimization), custom `get_search_results` overriding search to filter by `cascade_payment_method_id` GET param or by parsing the `HTTP_REFERER` URL for a cascade ID (regex-based, fragile — needed for the cascade-item inline autocomplete), `autocomplete_view` override (thin passthrough), actions = clear_cache_by_currency + clear_all_payment_methods_cache.
- `MerchantPaymentMethodAdmin`: large `list_editable`, `autocomplete_fields`, actions = clear_cache_by_merchant + clear_all_payment_methods_cache.
- `CompanyBalanceAdmin`: `get_queryset` prefetches `SellingInfo` via `Prefetch(..., to_attr='selling_infos')`; three computed display methods (`get_conversion_coefficients`, `available_balance_in_usdt`, `our_balance_in_usdt`, `clients_founds_in_usdt`) that manually look up the `USDTTRC` conversion coefficient from the prefetched list and divide — this USDT-conversion-display logic is duplicated near-identically in `MerchantBalanceAdmin` and should be centralized in the FastAPI rewrite.
- `CompanyMethodStatisticsAdmin`: defined, **never registered** (dead code — confirm before porting whether it should be).
- `FloatedProcentsCompaniesAdmin`: standard.
- `PaymentMethodTemplateAdmin`: `TemplateMethodMappingInline` (TabularInline), custom `get_methods_count` display.
- `PaymentMethodCascadeAdmin`: **the most complex admin class**:
  - `PaymentMethodCascadeItemInline` — `formfield_for_foreignkey` filters the `payment_method_company` dropdown by parsing `object_id` out of `request.resolver_match.kwargs` so the inline only offers companies matching the parent cascade's payment method; `get_formset` wraps the formset in a `ValidatedFormSet` subclass that raises `ValidationError` on cross-payment-method mismatches or duplicate priorities within the same cascade.
  - Custom `PaymentMethodNameFilter(SimpleListFilter)` — builds filter options from a distinct query rather than FK choices, to avoid registering unrelated `PaymentMethod`s as filter options.
  - `get_form`: identical fields for add/edit but adds contextual help text on edit; `get_readonly_fields`: makes `payment_method` readonly once a cascade exists (must not change after creation, since cascade items depend on it); `get_inlines`: hides the item inline entirely on the "add" form (items only make sense once the cascade exists); `response_add`: redirects to the change view after creation with a success message (so items can immediately be added) — this is a **custom admin flow, not stock Django add/redirect behavior**; `get_queryset`: heavy `Prefetch`+`annotate(Count('items'))`; `save_formset`/`save_model`: extra validation beyond `clean()` (duplicate-name-per-payment-method check) raising `ValidationError` at save time.

**personal_account_auth**
- `MerchantAdmin`: two custom bulk **admin actions** with full intermediate-page UX (not one-click):
  - `apply_template_to_merchants` — renders `select_template.html`, lets staff pick a `PaymentMethodTemplate`, then bulk-creates `MerchantPaymentMethod` rows for every merchant×method pairing not already existing (`bulk_create` inside `transaction.atomic()`), skipping existing pairs.
  - `apply_selected_methods_to_merchants` — a dynamic form (`PaymentMethodSelectionForm`) that, once currency+direction are chosen, dynamically injects one boolean field per matching `PaymentMethod` (`method_<id>`) so staff can hand-pick which methods to attach, with shared rate/limits/test_mode/only_admin_configure applied to all selected merchants. Same dedupe+bulk_create+atomic pattern.
- `MerchantBalanceAdmin`: `change_list_template` override (adds a custom button to the changelist template), custom `get_urls()` adding `refresh-balances/` — a **raw SQL admin view** (`refresh_balances`) that recomputes `balance`/`blocked_balance_in`/`blocked_balance_out` for every merchant/currency pair directly from the `transaction` table via a single `UPDATE ... FROM (SELECT ... GROUP BY)` statement, gated by `request.user.is_superuser` (the only explicit permission check found in the whole admin surface). Several `*_display` methods reformatting USDT-denominated fields via a shared `format_decimal` util (`utils.py`).
- `WhiteListAdmin`, `InviteTokenAdmin`: standard list/filter.
- `UserConfigAdmin`: `fieldsets` grouping USDT-rate-source config under a labeled section.

**personal_account_transaction** (the biggest admin.py, 721 lines)
- `TransactionAdmin` (extends `ImportExportModelAdmin` from `django-import-export`, with a `TransactionResource` defining export column names/order — CSV/Excel export is a first-class admin feature here):
  - `TransactionAdminForm` injects merchant/partner commission rates as `data-*` HTML attributes onto the `amount` field so a client-side JS file (`admin/js/transaction_changes.js`, loaded via `class Media`) can live-compute commission previews in the browser — **client-side calculation logic that must be ported to the new frontend/backend, not just the model**.
  - `get_fields`/`get_readonly_fields`: swaps two real Decimal fields (`usdt_fixed_course`, `amount_after_commission_in_usdt`) for read-only display-formatted versions on edit, but exposes the real editable fields on create.
  - Heavy `list_filter` set: custom `UniqueStatusFilter` (distinct-value driven), multi-select filters (`django-admin-multi-select-filter`) on merchant/company/payment-method/currency, and **both** `DateRangeFilter` and `DateTimeRangeFilter` (django-admin-rangefilter) on both date_create and date_update.
  - `save_model`: fetches the pre-edit `Transaction` via `TransactionService.get_transaction_by_id`, converts to DTO, and calls `obj.save(from_admin=True, old_transaction=old_transaction)` — wraps exceptions into an admin error message rather than raising (silently succeeds/half-saves on some errors — worth flagging for correctness in the rewrite).
  - **Admin action** `send_callbacks_to_merchants`: for each selected transaction, logs the callback payload/headers via `CallbacksService._get_callback_data_and_headers` (a "private" method being called from admin — encapsulation smell) then enqueues `CallbacksService.send_message_to_merchant.delay(transaction.id)` — a **Celery task dispatch from an admin action**.
- `AntiFraudBlockedMerchantUsersAdmin`: `list_editable` on ban-related fields, `save_model` diff-and-DTO pattern same as Transaction.
- `AppealAdmin`: defined but **not registered** with `@admin.register` or `admin.site.register()` — dead code, confirm intent before porting.
- `SettlementsAdmin`:
  - Custom `TransactionSettlementAdminForm`: dynamically scopes `balance_partner`/`balance_merchant` FK dropdowns to the instance's own company/merchant on edit; makes `conversion_rate` optional specifically for "exchanger" merchants (checked via `_is_exchanger_for_settlement`, which looks up `MerchantBalance→merchant→user→config.enable_usdt_exchanger`) with a form-level `clean()` re-validating that non-exchanger merchants must supply a conversion rate. **Note: `_is_exchanger_for_settlement` is defined twice verbatim** (lines ~452 and ~472) — literal duplicate function, latter shadows former.
  - `DynamicChoiceField`/`merchant_balance_label_for_settlement_admin`: custom FK label rendering showing USDT balance for exchanger merchants vs fiat balance otherwise.
  - `get_exclude`/`get_readonly_fields`: settlement type (`settl_type`) drives which fields become read-only/excluded post-creation (`FROM_PARTNER`/`TO_PARTNER` lock down `balance_partner`+more; `TO_MERCHANT`/`FROM_MERCHANT` additionally lock `clients_funds`/`our_funds`) — significant state-machine-like behavior baked into admin field visibility.
  - Custom `get_search_results` reimplementing OR-search across `search_fields` including related fields (duplicated verbatim from `TransactionAdmin`'s commented-out version — copy/paste candidate for a shared mixin).
  - `save_model`: same diff-DTO pattern, calls `obj.save(old_settlement=...)` which (per §2) creates/updates a linked `Transaction`.
  - **Admin action** `send_callbacks_to_tg_user`: dispatches `CallbacksService.send_message_to_tg_user(settlement.id)` — note this one is called **synchronously** (no `.delay()`), inconsistent with the Transaction admin's callback action which does dispatch async. Confirm intended behavior before porting.

**conversion_statistic**
- All three admins (`ConversionStatisticsAdmin` + presumably parallel classes for Partners/Merchant, same shape, truncated in review but structurally identical per the models) are `ImportExportModelAdmin` subclasses with a resource class mapping fields to human column names, **all fields readonly** (data is written only by background jobs, never edited by staff), multi-select + date-range filters.

**sandbox**
- `TestCreditsAdmin`: `fieldsets`, `list_editable` on `is_active`/`wanted_status_callback`, `get_queryset` select_related optimization. Nothing unusual.

**Cross-cutting admin observations**
- No `has_add_permission`/`has_change_permission`/`has_delete_permission`/`has_view_permission` overrides anywhere in any admin.py — access control is entirely stock Django `auth` permissions/groups (superuser or model-level `add_/change_/delete_/view_<model>` permissions), with the single exception of the manual `request.user.is_superuser` check inside `MerchantBalanceAdmin.refresh_balances`.
- Two custom `get_urls()` overrides found: `MerchantBalanceAdmin` (raw-SQL balance refresh endpoint) — that's the only one; no others in the reviewed admins.
- Consistent DTO-diff pattern: `save_model` fetches the old row via a Service layer, converts old+new to a `dataclass`-style DTO (`TransactionDTO`, `BlockListDTO`, `SettlementsDTO`), and lets a "Save Service" in `business_logic/services/` decide side effects (balance mutation, cache invalidation, cross-model sync). **This Service layer, not the admin classes themselves, is where the real domain logic lives** — a faithful FastAPI port needs to reimplement `TransactionSaveService`, `SettlementSaveService`, `BlockListService`, `CallbacksService`, `PaymentMethodService`, and `cache_invalidation.invalidate_cache`, not just the CRUD screens.

---

## 4. Auth & permissions model

- **`AUTH_USER_MODEL` is NOT customized** — the project uses Django's stock `django.contrib.auth.models.User` (confirmed via `personal_account_auth/models.py: from django.contrib.auth.models import User` and `get_user_model()` usage). No custom user app.
- **Merchant/tenant concept**: `Merchant` (personal_account_auth) is a FK-linked profile row on top of `User` — one `User` can own multiple `Merchant`s (`unique_together(user, name)`), each with its own `public_key`/`private_key` API credentials (plaintext in DB, auto-generated via `secrets.token_hex`). This is the **merchant-tenant boundary for payment configuration** (each Merchant has its own `MerchantPaymentMethod`, `MerchantBalance`, `WhiteList`), but it is **not** the same as the "brand" concept the new FastAPI service needs (RajaPay/AmPay/quiet-forest) — those are three **entirely separate database instances of this same codebase**, not rows within one DB. There is no `Brand` model; brand identity in this codebase is expressed only as hardcoded settings/URLs per deployment (see §10).
- **Admin authentication**: standard Django session-based admin login (`django.contrib.admin` + `AuthenticationMiddleware` + `SessionMiddleware`), admin mounted at `v2/{ADMIN_URL_KEY}/` (obscured URL, default `"admin"`, overridable via Vault-sourced `ADMIN_URL_KEY`).
- **API authentication** (separate from admin, relevant context only): DRF `JWTAuthentication` (`rest_framework_simplejwt`, access token lifetime 5 days) + `TokenAuthentication`, plus **Djoser** for user registration/JWT issuance, plus a bespoke **"magic link"/"magic ticket"** bearer-token flow (`personal_account_auth/business_logic/services/magic_ticket.py`, settings `MAGIC_LINK_*`) for merchant-dashboard SSO, and a separate static bearer token gate for a `registration_meta_data` endpoint (see hardcoded-secret flag in §5).
- **Row-level permission logic**: none found beyond the FK-scoped querysets in admin `get_queryset`/`formfield_for_foreignkey` overrides (which scope *dropdown choices*, not row visibility) and the WhiteList IP allow-listing (enforced elsewhere, likely in `lk` views, not admin). Django admin itself shows **all rows to any staff user with model permissions** — there is no per-merchant or per-brand row scoping inside the admin. This is an important gap for the new multi-tenant service: the old admin had a single flat permission model (Django groups/permissions) with no brand/tenant row filtering baked in, because each brand ran its own separate DB/admin instance.

---

## 5. Secrets & config management

**Loading mechanism** (`core/vault_loader.py` + `core/settings.py`):
- Primary source is **HashiCorp Vault** (`hvac` client, KV v2 engine), read via userpass auth (`VAULT_USERNAME`/`VAULT_PASSWORD` → token, with retry/backoff `get_vault_token_with_retry`). Three Vault paths are read into dicts and injected as globals into `settings.py`: `VAULT_URLS` (mount `urls`), `VAULT_SETTINGS` (mount `settings`), `VAULT_API_KEYS` (mount `api_keys`) — merged via `globals().update(...)` at module load, i.e. **any key present in Vault silently becomes a Django setting name** (implicit, not an explicit allowlist).
- **Fallback**: if `VAULT_ADDR` env var is absent, falls back to `python-dotenv` loading a local `.env` file (`load_dotenv(BASE_DIR/.env)`). If Vault auth/read fails outright, `get_vault_secrets` returns `{}` and every `VAULT_SETTINGS.get(...)` call in `settings.py` falls through to its Python-literal default (many of which are **weak or empty defaults** — see below).
- A short-lived allowlist of env vars (`keys_list`) is explicitly `os.environ.pop()`'d at the end of `settings.py` load — an attempt to scrub Vault-bootstrap credentials (`VAULT_ADDR`, `VAULT_USERNAME`, `VAULT_PASSWORD`, DB creds, etc.) out of the process environment after settings load, presumably to reduce exposure via `os.environ` dumps/debug pages later in the request lifecycle.

**Secret-like settings and their sourcing**:
| Setting | Source | Note |
|---|---|---|
| `SECRET_KEY` | `VAULT_SETTINGS.get("SECRET_KEY", "django_secret_key")` | **Weak hardcoded fallback** if Vault is unreachable. |
| `POSTGRES_PASSWORD` | Vault, or hardcoded `"postgres"` when `is_tests` truthy (local/test DB) | Test-only fallback is fine, but the truthiness check (`is_tests = DB_HOST_VAL or DB_HOST_TEST_VAL`) is broad — any non-empty `DB_HOST`/`DB_HOST_TEST` env var, not just `"localhost"`, sets `POSTGRES_PASSWORD="postgres"` per line 51's `in [...]` check actually narrows it to specific literal hostnames, so this is scoped correctly on inspection — flag as needing test coverage, not a live bug. |
| `POSTGRES_DB`, `POSTGRES_USER` | Vault only, no fallback (`None` if missing → will hard-fail DB connection) | |
| `DJANGO_SUPERUSER_PASSWORD` | `VAULT_SETTINGS.get(..., "admin")` | **Hardcoded weak fallback** for bootstrap superuser. |
| `ADMIN_PUBLIC_KEY` / `ADMIN_PRIVATE_KEY` | Vault only | Purpose unclear from settings.py alone (not referenced elsewhere in files read); likely used by a payment gateway integration for signing. |
| `MAGIC_LINK_ISSUER_BEARER_TOKEN` | `VAULT_SETTINGS.get(..., "") or "mk_issuer_token_dfnglkdsfjgnldskfgdjf34g59rniegotgjklfdngkfgjdfgnfdlgskjrnoagwekrng"` | **Hardcoded secret fallback, `core/settings.py:95`.** Note this fallback string's suffix (`dfnglkdsfjgnldskfgdjf34g59rniegotgjklfdngkfgjdfgnfdlgskjrnoagwekrng`) is **byte-for-byte identical** to a separately hardcoded constant `MERCHANT_METADATA_ACCESS_TOKEN` in `personal_account_auth/presentation/views/registration_meta_data.py:97` — i.e. this is a real, live, shared bearer secret checked into source twice, not a placeholder. **Must not be carried into the new service as a default; must be sourced from a secrets manager only.** |
| `AGENT_REFERRAL_CALLBACK_SECRET` | `VAULT_SETTINGS.get(..., "")` | Empty-string fallback effectively disables signature verification if unset — verify the consuming code treats empty as "no secret configured" safely. |
| DB host/port | env vars first (`DB_HOST`, `DB_PORT`, etc.), then Vault | |
| Redis host/db/celery broker/result backend | Vault only, brand-suffixed keys (`REDIS_HOST_RAJAPAY`, `CELERY_BROKER_URL_RAJAPAY`, etc.) | **Confirms the "one codebase, brand-suffixed Vault keys" pattern** — see §10. |
| `COMPANY_EMAIL` | `VAULT_SETTINGS.get(..., "rajapay.dev@gmail.com")` | Hardcoded brand-specific fallback email. |
| Payment-gateway API keys (`CASHONRAILS_SECRET_KEY_NGN`, `OFFSETPAY_API_KEY`, `PAYPORT_API_KEY`, `AGGREPAY_TOKEN`, `BRUSNIKA_JWT_KZT_TOKEN`, `RIZON_API_KEY`, `PROTOCOL_API_TOKEN_KZT`, `AIRPAY_API_KEY_AZN`, `MERCHANT_PRIVATE_KEY_KZF/KZS/CARD/...`, dozens more) | Scattered as module-level constants in `api_mediator/business_logic/api_services/<gateway>/constants.py`, presumably populated from `VAULT_API_KEYS` globals-merge (not individually confirmed per file, but consistent with the `globals().update(VAULT_API_KEYS)` pattern in settings.py) | **Out of scope for the admin-only migration** (these live in the payment-execution engine, not admin.py), but relevant if the new service ever needs to *display* gateway credentials in an admin UI — do not hardcode them. |
| `DAXCHAIN_TEST_API_KEY = "ruvhIOY1rn2C0oKhBhY5N2UyeGt9Sm5l"` (`api_mediator/business_logic/api_services/daxchain/daxchain_service.py:23`) | **Hardcoded literal** (named "TEST", likely intentionally non-prod, but still a real key value in source — flag for review). | |
| `AGGREPAY_TOKEN = ""` (`api_mediator/business_logic/api_services/aggrepay/constants.py:6`) | Hardcoded empty — likely a stub/placeholder rather than a real leaked secret, but confirm before assuming it's inert. | |

**Not applicable / no secrets manager beyond Vault**: no AWS Secrets Manager, no `django-environ`, no `python-decouple`; just Vault + `.env`/`os.environ` + Python-literal defaults.

---

## 6. Database

- **Engine**: `django.db.backends.postgresql_psycopg2` (i.e. Postgres via `psycopg2-binary==2.9.9`), single `DATABASES["default"]` entry — **no database routers, no multi-DB config, no read replicas** configured in this codebase. (Confirms the premise: multi-tenancy across brands is achieved by running three *separate deployments* of this same codebase against three separate Postgres databases, not by a DB router within one deployment.)
- Host resolution has a DEBUG/test wrinkle: `HOST = DB_HOST if not DEBUG else DB_HOST_TEST` — i.e. in `DEBUG=True` mode it connects to a different host (`DB_HOST_TEST`) than prod.
- `DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"` — all PKs are `BIGINT`.
- Field types worth flagging for a SQLAlchemy port:
  - Heavy use of **`DecimalField`** with inconsistent precision across the schema for what is conceptually "money": `max_digits=10,decimal_places=2` (Transaction amounts), `18,2` (balances/commissions), `18,4`/`20,4` (statistics sums), `14,6` (SellingInfo.coefficient), `18,8`/`20,8` (USDT amounts and fixed courses), `5,2` (percentage rates), `26,2` (FloatedProcentsCompanies bounds), `15,2` (Settlements USDT sums). **A FastAPI/SQLAlchemy rewrite should not assume one canonical `Numeric` precision** — needs a field-by-field precision audit, especially at the fiat/USDT boundary where `usdt_fixed_course` (18,8) divides into `amount_after_commission` (10,2) to produce `amount_after_commission_in_usdt` (20,8).
  - **`JSONField`**: `UserConfig.manual_usdt_rates` (dict of ISO→rate strings), `TestCredits.requisite_details`. Both need NOT NULL defaults handled explicitly in the new ORM layer (Django's admin submits `null` for empty JSON widgets; the model's `save()` coerces to `{}}` — SQLAlchemy will need the same guard or a server-side default).
  - **String-typed "soft FKs"**: `Settlements.transaction_id` is a `CharField`, not an FK to `Transaction` — it's manually kept in sync with `Transaction.tracker_id` inside `Settlements.save()`. Same for `AntiFraudBlockedMerchantUsers.user_id` (merchant's own external user id, not a Django-relation). These need explicit application-level consistency logic in the rewrite since the DB won't enforce it.
  - **Auto-generated identifiers**: `Merchant.public_key`/`private_key`, `InviteToken.token`, several `Transaction.*_id` fields default to UUIDs/hex tokens generated in `save()`/`from_admin` paths, not DB defaults — must be replicated in application code, not left to the DB.
  - `unique_together` is used extensively instead of `UniqueConstraint` (Django 4.2-era style) — straightforward to map to SQLAlchemy `UniqueConstraint`/composite unique indexes.
  - No soft-delete pattern anywhere (no `is_deleted`/`deleted_at` fields) — deletions are hard deletes via standard Django admin delete action, protected only by FK `on_delete` behavior (mostly `CASCADE`, some `SET_NULL`).

---

## 7. Domain models relevant to a payments admin (summary table)

| Concern | Model(s) | Admin-exposed operations |
|---|---|---|
| Transactions | `Transaction` | Full CRUD via admin, with `from_admin=True` triggering `TransactionSaveService` side effects (balance sync); bulk CSV/XLSX export (`django-import-export`); admin action to (re)send merchant callbacks (async Celery dispatch); heavy filtering (status/direction/date range/merchant/partner/method/currency, all multi-select). |
| Merchants | `Merchant`, `MerchantBalance`, `WhiteList`, `UserConfig` | CRUD; bulk-provision payment methods to merchants via two custom actions (template-based, or hand-picked); raw-SQL "refresh balances from ledger" admin view (superuser-gated); IP allowlisting CRUD. |
| Partners/gateways | `Company`, `PaymentMethod`, `PaymentMethodCompany`, `PaymentMethodCascade(Item)` | CRUD on partner configs, rates, limits, routing priority (much of it `list_editable` inline); cascade-based routing configuration with custom validation; cache-invalidation admin actions (Redis) whenever routing/rate config changes. |
| Balances/ledger | `CompanyBalance`, `MerchantBalance`, `Settlements` | CRUD, with computed USDT-equivalent display columns; `Settlements` CRUD triggers creation/mutation of a linked `Transaction`; alert thresholds (`alert_balance_percent`/`insurance_balance`) feed a Celery balance-alert task (webhook to n8n) — closest thing to a "payout/reserve monitoring" feature. |
| Disputes | `Appeal` | Admin-registered (though not `@admin.register`'d — needs confirmation) list view with an inline photo-evidence preview; status field reuses `Transaction`'s StatusChoices including an `APPEAL` state. |
| Fraud/risk | `AntiFraudBlockedMerchantUsers` | Ban/second-chance workflow with counters and timestamps auto-computed on state transitions; `list_editable` for quick ban toggling. |
| KYC | **None found.** No KYC/verification/document model exists anywhere in the reviewed apps. |
| API keys | `Merchant.public_key`/`private_key` | Auto-generated, plaintext, shown in admin list/detail — this *is* the merchant API-credential model; there is no separate "APIKey" model with scopes/rotation/expiry. |
| Webhooks/callbacks | No persisted "Webhook" model — callback delivery is stateless, driven by `CallbacksService` (business logic, not a DB-backed config) using `Transaction.callback_url`/`Merchant`. `AGENT_REFERRAL_CALLBACK_URL`/`_SECRET` (settings) suggest an outbound webhook to an external "agent referral" system, and `N8N_WEBHOOK_URL`/`N8N_USDT_RATE_FALLBACK_WEBHOOK_URL` (settings + tasks.py) are outbound integration points to n8n automation, not modeled entities. |
| Invoices | **None found.** No Invoice model. |
| Sandbox/test tooling | `TestCredits` | CRUD, toggle active/desired callback outcome — used by merchants integrating against a sandbox environment. |
| Reporting/statistics | `ConversionStatisticsNew/PartnersNew/MerchantNew` | Read-only in admin; exportable; populated by background jobs (not shown in files read, likely Celery or management commands, given the `date_only`/rollup shape). |

---

## 8. Background jobs / Celery triggered from (or feeding) admin

- `core/settings.py` `CELERY_BEAT_SCHEDULE`: `api_client.tasks.form_selling_info_for_available_fiats` (15 min), `api_mediator.tasks.update_payment_methods_company_daily_limits` (daily at midnight — resets the `current_daily_*` counters admins see on `PaymentMethodCompanyAdmin`), `api_mediator.tasks.check_company_balances_alerts` (every 30 min), `api_client.tasks.update_usdt_rates` / `update_usdt_rates_for_exchanger_users` (every 30s each) — these populate `SellingInfo.coefficient`, the value every USDT-display admin method (`available_balance_in_usdt`, etc.) depends on.
- **Admin-triggered Celery dispatch**: `TransactionAdmin.send_callbacks_to_merchants` action calls `CallbacksService.send_message_to_merchant.delay(transaction.id)` (async). `SettlementsAdmin.send_callbacks_to_tg_user` calls `CallbacksService.send_message_to_tg_user(settlement.id)` **without `.delay()`** — runs synchronously inside the admin request if that method is itself a `@shared_task` (needs verification against `callbacks.py`, not fully read, but the asymmetry with the Transaction action is worth flagging as either a bug or an intentional immediate-send).
- `api_mediator/tasks.py` also defines non-beat, presumably API-triggered tasks not invoked from admin: `execute_after_timeout` (transaction timeout auto-decline), `send_callback_to_sandbox` (sandbox test-credit callback simulation, with a **hardcoded list of magic test wallet/phone values** that trigger a forced SUCCESS), `check_company_balances_alerts` (posts to a **hardcoded n8n webhook URL**, `https://n8n.quiet-forest.su/webhook/...` — see §10), `clearix_check_payment_status` (polls an external gateway's status endpoint and pushes state via `CallbacksService`).
- `apps.py` `post_migrate` signal receivers in `api_client` and `api_mediator` run idempotent DB-population scripts (seed `Bank`/`Currency`/`SellingInfo`/`Company`/`PaymentMethod`/`PaymentMethodCompany` rows) on every migrate — not admin-triggered but relevant to know if the new service needs equivalent seed/bootstrap data.

---

## 9. Dependencies relevant to admin/auth/DRF/secrets (`requirements.txt` / `pyproject.toml`)

- **Django 4.2.3**, `djangorestframework==3.14.0`, `djangorestframework-simplejwt==5.2.2` (+ `token_blacklist` app), `djoser==2.2.3`, `drf-spectacular==0.27.0` (OpenAPI schema/Swagger UI).
- **Admin UX add-ons**: `django-import-export==3.3.3` (CSV/XLSX export used by Transaction/ConversionStatistics admins), `django-admin-rangefilter==0.11.2` (date range filters), `django-admin-multi-select-filter==1.3.0`, `django-more-admin-filters==1.7`, `django-select2==8.1.2` (not confirmed wired into any admin read, but installed).
- **Secrets/infra**: `hvac==2.3.0` (Vault client), `python-dotenv==1.0.0` (`.env` fallback), `cryptography==42.0.8`, `PyJWT==2.8.0`, `pycryptodome==3.20.0`.
- **Celery/cache**: `celery==5.3.6`, `redis==5.0.1`, `django-redis==5.4.0`.
- **CORS**: `django-cors-headers==4.3.1`.
- **Health checks**: `django-health-check==3.20.0` (mounted at `v2/ht/`).
- **Currency data**: `iso4217==1.11.20220401`, `pycountry==24.6.1` (used to build `Currency.iso_code` choices).
- **HTTP clients used by gateway integrations**: `requests==2.31.0`, `httpx==0.28.1`.
- **social-auth-app-django/social-auth-core**: installed but no reference found in the files read for admin/auth flows reviewed — likely used by `lk`/merchant-facing OAuth, not the admin panel; verify before assuming unused.
- No FastAPI/SQLAlchemy/Pydantic-for-API packages present yet (Pydantic 2.5.3 is present but appears used only for internal DTOs, e.g. `dacite==1.8.1` alongside it suggests dataclass↔dict conversion for the DTO pattern in `business_logic/services`, not a web framework).

---

## 10. Other architecturally notable points / brand divergence

- **Django signals**: only `post_migrate` receivers in `api_client/apps.py` and `api_mediator/apps.py` (idempotent seed-data population). No `pre_save`/`post_save`/`pre_delete` signals found — all the "side effect on save" behavior is done by **overriding `Model.save()` directly** and threading extra kwargs (`from_admin=True`, `old_transaction=...`, `old_value=...`, `old_settlement=...`) from `ModelAdmin.save_model()` into it. This is an unusual, admin-coupled pattern: the model's `save()` method behaves differently depending on whether it was called from the admin vs. elsewhere (e.g., the API), and the "elsewhere" callers must remember to pass the right DTOs. **A FastAPI rewrite must not treat these as plain ORM `save()` calls** — the admin-specific branch encodes real business rules (UUID backfill, USDT recompute, cross-model balance/transaction sync) that have no equivalent trigger if ported as passive columns.
- **Business logic embedded in `admin.py` beyond simple display formatting**: cascade validation state machine (`PaymentMethodCascadeAdmin`/`PaymentMethodCascadeItemInline`), settlement-type-driven field visibility state machine (`SettlementsAdmin.get_exclude`/`get_readonly_fields`), bulk merchant-provisioning workflows with two-step confirm pages (`MerchantAdmin` actions rendering custom templates `select_template.html`/`select_methods.html`), and a raw-SQL "recompute the world" endpoint (`MerchantBalanceAdmin.refresh_balances`). All of these need explicit reimplementation as FastAPI endpoints/services — they are not something an auto-generated admin CRUD scaffold will reproduce.
- **Client-side JS coupled to admin forms**: `admin/js/transaction_changes.js` and `admin/js/transaction_settlement.js` (paths under `personal_account_transaction/static/admin/js/` and `api_mediator/static/admin/js/`) perform live commission/amount calculations in the browser using `data-*` attributes injected by the Django form (`TransactionAdminForm.__init__`). This logic is invisible if you only port `admin.py`/`models.py` — worth pulling these JS files during migration to understand exactly what calculation the new frontend needs to reproduce.
- **Duplicate/dead code found** (useful to flag to the team, may or may not be intentional):
  - `_is_exchanger_for_settlement` defined twice, verbatim, in `personal_account_transaction/admin.py`.
  - `CompanyMethodStatisticsAdmin` and `AppealAdmin` defined but never registered with Django admin (`@admin.register` absent, no `admin.site.register()` call found) — confirm whether these are meant to be admin-visible before porting.
  - `clear_cache_by_method_currency` action defined but never attached to any `ModelAdmin.actions`.
  - Three near-identical `save()` implementations across `ConversionStatisticsNew/PartnersNew/MerchantNew` (percentage recompute + None-coercion) — good candidate to collapse into one shared function/mixin in the rewrite rather than tripling the logic again.
- **Brand/multi-tenant divergence signals** (directly answers the "AmPay/RajaPay/quiet-forest" divergence question):
  - Vault settings are **brand-suffixed by convention** (`HOST_RAJAPAY`, `REDIS_HOST_RAJAPAY`, `REDIS_HOST_TEST_RAJAPAY`, `CELERY_BROKER_URL_RAJAPAY`, `CELERY_RESULT_BACKEND_RAJAPAY`, `DOMAIN_NAME_RAJAPAY`, `SERVER_IP_RAJAPAY`, `COMPANY_EMAIL_RAJAPAY`, `DJANGO_SUPERUSER_USERNAME_RAJAPAY`) — confirming this is genuinely the *same* settings.py deployed three times with different Vault key suffixes/mount paths per brand, not a shared multi-tenant settings file.
  - **But the RajaPay settings.py also directly defines `MAGIC_LINK_FRONTEND_URL_AMPAY`, `MAGIC_LINK_FRONTEND_URL_QF`, `AMPAY_EMAIL`, `AMPAY_DOMEN`, `HPP_DOMEN_AMPAY`** — and several of these (`AMPAY_EMAIL = COMPANY_EMAIL`, `AMPAY_DOMEN = DOMAIN_NAME`, `HPP_DOMEN_AMPAY = HPP_DOMEN_RAJAPAY`, `MAGIC_LINK_FRONTEND_URL_AMPAY = MAGIC_LINK_FRONTEND_URL_RAJAPAY`) are **hardcoded aliases back to the RajaPay values**, at `core/settings.py` lines 288–293. This means the RajaPay deployment's settings module *pretends* to also configure AmPay, but actually just re-exports its own RajaPay values under AmPay-named settings — a copy-paste leftover from a shared settings template that was never cleaned up per-brand. **This is a concrete divergence risk**: if any code path in this repo reads `settings.AMPAY_EMAIL` expecting AmPay's real values, it silently gets RajaPay's values instead. The new multi-tenant FastAPI service must not copy this aliasing pattern — brand-specific values must come strictly from the JWT's `brand_id`-selected config, never from another brand's settings as a fallback.
  - `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`/`CORS_ALLOWED_ORIGINS` are hardcoded with `raja-pay.com`/`quiet-forest.su`-style domains and long lists of literal IPs — brand identity is baked into network config, not parameterized.
  - `api_mediator/tasks.py` hardcodes an **n8n webhook URL on the quiet-forest.su domain** (`N8N_WEBHOOK_URL = "https://n8n.quiet-forest.su/webhook/..."`) inside the **RajaPay** codebase's balance-alert task — i.e. RajaPay's own balance alerts fire against quiet-forest's automation infrastructure. Either this is genuinely shared n8n infra across all three brands (in which case fine, but should be Vault-sourced per brand, not hardcoded to one brand's domain), or it's a copy-paste bug where RajaPay is alerting into quiet-forest's ops channel. **Flag this explicitly to the team** — it's exactly the kind of cross-brand leakage the new brand-scoped JWT architecture needs to eliminate.
  - `MERCHANT_TIMEOUTS_MIN` (settings.py bottom) contains a **hardcoded per-merchant override keyed by a specific merchant hash** (`"1ea83fc656...": 20`) with a comment `# Hello123` — a one-off business rule for a named merchant baked directly into source rather than being data-driven via the DB. Same anti-pattern as the AMPAY aliasing: brand/tenant-specific exceptions living in code instead of config/DB rows. The new service should model this as merchant-level config data, not a settings constant.
  - `AVAILABLE_FIAT`, `TRANSACTION_BANK`, `FIAT_LIMIT`, `BANKS` (settings.py) are large hardcoded currency/bank tables — likely identical or near-identical across all three brand codebases; worth diffing against the AmPay/quiet-forest repos directly to see if these tables have actually drifted (different limits, different bank defaults per brand) since they encode business rules (per-currency transaction limits) that a multi-tenant service will need to key by `brand_id`.

---

## Suggested priorities for the FastAPI admin service

1. Treat `TransactionSaveService`, `SettlementSaveService`, `BlockListService`, and the cache-invalidation service as **required ports**, not optional — the admin's model `save()` methods are thin wrappers around them.
2. Rebuild the settlement→transaction sync (Settlements.save()) and the balance "refresh from ledger" raw SQL as explicit, tested service functions rather than ORM side effects — this is the highest-risk logic to get subtly wrong.
3. Decide deliberately how brand scoping replaces the current "one settings.py per brand deployment" model — do **not** port the AMPAY-aliasing pattern or the hardcoded n8n domain; every brand-specific value (email, domain, webhook URL, currency limits, magic-link frontend URL) should be resolved from the brand_id in the JWT against per-brand config, with no cross-brand fallback.
4. Purge the hardcoded `MAGIC_LINK_ISSUER_BEARER_TOKEN`/`MERCHANT_METADATA_ACCESS_TOKEN` fallback secret and the weak `SECRET_KEY`/`DJANGO_SUPERUSER_PASSWORD` defaults; the new service should fail closed (refuse to start) if a required secret is missing, not fall back to a hardcoded value.
5. Since no row-level/tenant-scoped permission logic exists today (Django admin shows all rows to any staff user with model perms), the new service needs to *add* brand-scoped and possibly merchant-scoped row filtering that didn't exist before — this is new work, not a port.
