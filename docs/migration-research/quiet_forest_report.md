# quiet-forest Django Monolith — Admin-Panel Migration Research Report

Repo analyzed: `/scratchpad/monoliths/quiet-forest/quiet-forest`
Read-only research pass in support of migrating the Django admin logic to a standalone FastAPI multi-tenant admin microservice.

---

## 0. Brand identity

No brand name other than **"quiet-forest"** appears anywhere in the code, `pyproject.toml` (`name = "quiet-forest"`), domains (`quiet-forest.su`, `quiet-forest.online`), nginx configs, or Redis/container hostnames (`redis_test_qf`, `nginx_prod_qf`). The abbreviation **"QF"** is used consistently in infra names. There is no separate/real trading name hidden in the repo — "quiet-forest" is baked into the code itself, not just an external codename layered on top by the researcher. Nothing indicates this is literally the real-world brand (it reads like a placeholder brand used across all three monolith clones), but no other candidate name was found anywhere in code, docs, static assets, or config. Flag this back to the user: the "real brand name" the task hints might be discoverable was **not found** — only "quiet-forest"/"QF" appears.

Static assets include `qf_logo.png` (reinforcing the "QF" branding) and India-specific UPI/PhonePe/Paytm/IMPS/NEFT/RTGS assets plus Russian-bank card art (Sberbank/VTB/Alfa/SBP), suggesting this brand serves both Indian and Russian/CIS payment corridors.

---

## 1. Project layout (Django apps)

| App | Purpose |
|---|---|
| **core** | Django project root: `settings.py`, `urls.py`, `celery.py`, `vault_loader.py` (secrets), logging config. |
| **api_client** | Client-facing surface: `Currency`, `Bank`, `Card`, `SellingInfo` (FX conversion rates) models; P2P payment window views/templates (including India-specific redirect flows); Celery tasks for USDT rate updates and daily "selling info" computation; integrations with Binance/Bybit/Rapira rate sources. |
| **api_mediator** | The payment-gateway integration core. Contains ~90+ sub-packages under `business_logic/api_services/` (one per payment provider — see §7), each with a `*_service.py` (outbound calls) and `*_callback.py` (inbound webhook handling). Also owns the domain models for `Company` (payment provider/partner), `PaymentMethod`, `PaymentMethodCompany`, `MerchantPaymentMethod`, `CompanyBalance`, cascading/failover routing (`PaymentMethodCascade*`), and per-partner daily limits/statistics. Celery tasks reset daily limits and check company balance alert thresholds. |
| **bin_checker** | Small lookup app: `BanksNames` (with NSPK — Russian card-scheme — code) and `BinNumbers` (6-digit BIN → bank), used to identify a card's issuing bank from its number. |
| **conversion_statistic** | Rolls up per-day conversion funnel metrics (`ConversionStatisticsNew` for methods, `...PartnersNew` for partner methods, `...MerchantNew` for merchant methods) — requests → success → paid-orders funnel with computed conversion %. Export-only via admin (all fields readonly). |
| **personal_account_auth** | Auth/identity: custom `Merchant` model (1:1-ish wrapper around Django `User` holding API keys), `MerchantBalance` (per-currency wallet), `WhiteList` (per-merchant allowed IPs — legacy, superseded by the `whitelist` app), `InviteToken` (invite-gated signup), `UserConfig` (per-user USDT-exchanger rate-source preferences). Uses Djoser + SimpleJWT for merchant-facing REST auth. |
| **personal_account_transaction** | The transactional ledger: `Transaction` (core payment record), `Settlements` (internal ledger movements between merchant/partner balances), `Appeal` (dispute/chargeback-like transaction appeal with photo evidence), `AntiFraudBlockedMerchantUsers` (per-merchant end-user blocklist with "second chance" grace logic), `BrokenRequisite`/`RequisiteSettings` (auto-blocking of broken P2P card requisites after N failures). Contains the merchant-facing "window" (payment page) views and cache-invalidation logic used heavily by `api_mediator`'s admin actions. |
| **whitelist** | Newer IP-allowlist app: `WhitelistsMerchant` and `WhitelistsPartner`, each `(entity, ip)` unique pairs with IPv4/IPv6 validation. |
| **sandbox** | Test/sandbox mode support: `TestCredits` — fake requisites mapped to a `PaymentMethod`, with a desired callback status (`SUCCESS`/`DECLINED`/etc.) for exercising the payment flow without a real partner. |
| **lk** | "Личный кабинет" (personal account) — merchant-facing dashboard/service layer, not admin; not deeply explored since it's outside admin scope (no `admin.py`/`models.py`). |
| **utils** | Shared helpers (`format_decimal`, `Reform` pretty-printer, misc scripts). Not a Django "app" in the INSTALLED_APPS sense beyond potential utility imports. |
| **conf** | Nginx configs for prod/test/admin/ref servers (infra, not Django). |
| **docs** | OpenAPI spec + Redoc HTML (merchant-facing API docs, not admin-panel-related). |

INSTALLED_APPS also includes: `rest_framework`, `rest_framework_simplejwt` (+ `token_blacklist`), `corsheaders`, `import_export`, `rangefilter`, `more_admin_filters`, `django_select2`, `drf_spectacular`, `djoser`, `health_check` (+ db/cache/storage/migrations/celery/celery_ping/db_heartbeat sub-checks).

---

## 2 & 3. Models registered in Django admin, and their ModelAdmin customizations

### api_client (`api_client/admin.py`)
- **Currency** (`CurrencyAdmin`) — fields: `iso_code` (CharField, unique, choices from `iso4217` package), `addition_name`, `limit` (PositiveInteger), `binance_bank` (FK→Bank, nullable). Custom `CurrencyForm`. `list_display`/`search_fields` only, no custom actions.
- **Card** (`CardAdmin`) — bank card requisites with per-card transaction count/amount limits (current vs. max, upper/lower per-transaction bounds) and `is_enabled` toggle (`list_editable`). FK to `Currency`, `Bank`.
- **Bank** (`BankAdmin`) — simple name lookup.
- **SellingInfo** (`SellingInfoAdmin`) — FX conversion coefficient `currency_for_sale → currency_for_buy` (e.g. RUB→USDTTRC), `unique_together` on the pair; custom `formatted_date_update` display method using `timezone.localtime`.

### api_mediator (`api_mediator/admin.py`) — the payments-routing core
- **PaymentMethod** — `token` (auto-generated on save via `create_token(iso_code, name, sub_method)`), `currency` FK, `direction` (IN/OUT choice), `sub_method`. `unique_together` on `(name,currency,sub_method,direction)` and `(token,direction)`.
- **Company** (partner/PSP) — comment in code: *"Специально убрал регистрацию этой модели. Чтобы никто случайно не изменил что-то"* ("deliberately removed registration so nobody accidentally changes something") — yet it IS still `@admin.register`ed with full list/filter/search (the comment appears stale/contradicts the code).
- **PaymentMethodCompany** (a partner's configured instance of a payment method) — rich `list_editable` surface: `is_active`, `priority`, `partner_rate`, `additional_commission`, `settlement_commission`, `daily_amount_limit`, `daily_count_limit`, `transaction_min_limit/max_limit`. Read-only running counters: `current_daily_count`, `current_daily_coun_success` [sic], `current_daily_amount`, `current_daily_amount_success`, `last_reset`. Custom `get_search_results` special-cases a `cascade_payment_method_id` GET param and parses the HTTP referer URL to filter by cascade when editing inline. Custom **admin actions**: `clear_cache_by_currency`, `clear_all_payment_methods_cache` — both call `invalidate_cache()` (Redis-backed cache invalidation helper from `personal_account_transaction.../cache_invalidation`).
- **MerchantPaymentMethod** (merchant's configured instance of a method) — `list_editable` on `personal_rate`, `test_mode`, `additional_commission`, `block`, limits, `no_callback`, `only_admin_configure`, `cascade`. Actions: `clear_cache_by_merchant`, `clear_all_payment_methods_cache`.
- **CompanyBalance** — partner's per-currency balance ledger: `blocked_balance_in/out`, `available_balance`, `our_income`, `clients_funds`, `alert_balance_percent` (0–100 validated), `insurance_balance`. Admin computes **derived USDT-equivalent columns on the fly** (`available_balance_in_usdt`, `our_balance_in_usdt`, `clients_founds_in_usdt`) by looking up `SellingInfo` conversion coefficients via a prefetched `Prefetch` — this is business logic embedded directly in `admin.py` that must be reimplemented explicitly in FastAPI (it is NOT a model method).
- **CompanyMethodStatistics** — daily per-partner-method counters (not admin-registered as a decorator but defined; registration commented/omitted — verify at migration time, it has an `Admin` class defined but no `@admin.register`).
- **FloatedProcentsCompanies** — tiered/floating commission rate bands (`from_amount`→`to_amount`→`rate`) per `PaymentMethodCompany`.
- **PaymentMethodTemplate** / **TemplateMethodMapping** — reusable bundles of payment methods (with default rate/limits) that can be bulk-applied to merchants; `TemplateMethodMappingInline` (TabularInline) on the template admin.
- **PaymentMethodCascade** / **PaymentMethodCascadeItem** — ordered failover/priority routing lists of `PaymentMethodCompany` per `PaymentMethod`. Heavy admin logic: `PaymentMethodCascadeItemInline` with custom `formfield_for_foreignkey` (filters dropdown by cascade's payment method, parsed from `resolver_match.kwargs['object_id']`), a custom `get_formset` returning a `ValidatedFormSet` subclass that enforces payment-method consistency and unique priorities across inline rows, `get_readonly_fields`/`get_inlines`/`get_form` all overridden to change behavior between add vs. change views, and `save_model`/`save_formset` overrides enforcing cross-model uniqueness constraints (duplicate name+payment_method) beyond what `Meta.unique_together` can express purely via DB constraint at save-time in admin. Custom `PaymentMethodNameFilter(admin.SimpleListFilter)`. `response_add` redirects straight into the change view after creation (a UX nicety to reimplement).

### personal_account_auth (`personal_account_auth/admin.py`)
- **Merchant** — API-key pair (`public_key`/`private_key`, hex tokens auto-generated via `secrets.token_hex` on save if absent) tied 1:1(ish) to a Django `User` FK; `unique_together(user, name)`. **Custom admin actions with side effects and rendered intermediate forms** (two-step wizards, not simple one-click actions):
  - `apply_template_to_merchants` — renders `select_template.html`, bulk-creates `MerchantPaymentMethod` rows from a `PaymentMethodTemplate` for the selected merchants (skips existing pairs), wrapped in `transaction.atomic()`.
  - `apply_selected_methods_to_merchants` — renders `select_methods.html` with a dynamically-built form (`PaymentMethodSelectionForm` adds one `BooleanField` per matching `PaymentMethod` for the chosen currency+direction), then bulk-creates `MerchantPaymentMethod` rows.
  Both of these are non-trivial multi-step wizard UIs backed by admin actions rendering intermediate templates — a pattern that does not map 1:1 onto typical REST endpoints and needs explicit UI/flow design in FastAPI.
- **MerchantBalance** — per-merchant per-currency wallet with USDT variants (`balance_usdt`, `blocked_balance_usdt_in/out`, `insurance_balance_usdt` — note: comment in model explicitly says the DB column name `insurance_balance_usdt` is historically misnamed and holds a **fiat** value, not USDT — a landmine for a schema rewrite). Custom `change_list_template` overriding the list page (`merchantbalance_change_list.html`) and a **custom admin URL** (`get_urls` override) `refresh-balances/` → `refresh_balances` view that runs a **raw SQL UPDATE** recalculating `balance`/`blocked_balance_in`/`blocked_balance_out` from the `transaction` table (superuser-gated: `request.user.is_superuser`). This raw-SQL balance-reconciliation query is important business logic to port explicitly — it defines exactly how balances are derived from transaction history (SUCCESS/ACCEPTED, IN/OUT).
- **WhiteList** (legacy per-merchant IP allowlist, superseded conceptually by the `whitelist` app's `WhitelistsMerchant`/`WhitelistsPartner`).
- **InviteToken** — invite-gated signup token, auto-hex-generated, `used` flag, `invited_user` O2O.
- **UserConfig** — O2O to `User`; USDT-exchanger settings incl. a **`JSONField`** `manual_usdt_rates` (`{"KZT": "520.5", ...}`, default `{}`), plus `ExchangerTypeChoices`/`ExchangerSourceChoices` (`TextChoices`) governing whether rates come from a global cascade or a pinned exchange (Binance/Bybit/Rapira) with page/row-range parameters. Model `clean()` enforces conditional-required validation (pinned_exchange required if source=EXCHANGE_PINNED; row-range from/to must both be set or both empty).

### personal_account_transaction (`personal_account_transaction/admin.py`) — the largest/most complex admin surface
- **Transaction** (`TransactionAdmin(ImportExportModelAdmin)`) — the core ledger row. Key fields: `tracker_id`, `partner_system_id`, `merchant_system_id`, `merchant_client_id` (all UUID-defaulted string IDs, not real UUID fields), `status` (`StatusChoices`: ACCEPTED/SUCCESS/DECLINED/APPEAL), `direction` (IN/OUT), `amount`/`commission`/`partner_income`/`pure_our_income`/`amount_after_commission` (all `DecimalField(10,2)`), optional `usdt_fixed_course` and `amount_after_commission_in_usdt` (Decimal 18-20 digits, high precision for crypto). FK to `Merchant` and `PaymentMethodCompany`. Multiple partial/composite DB indexes (including a **conditional index** `idx_transaction_success_date` with `condition=Q(status="SUCCESS")` — Postgres-specific partial index, worth noting for the SQLAlchemy port). `unique_together(merchant_system_id, merchant)`.
  - Uses `django-import-export`'s `ImportExportModelAdmin` + a custom `TransactionResource` (CSV/XLSX export column mapping/renaming).
  - Custom `TransactionAdminForm` injects merchant/partner commission rates as `data-*` HTML attributes for client-side JS calculation (`admin/js/transaction_changes.js` — not reviewed, referenced via `class Media`).
  - **`save_model` override**: on edit, loads the prior DB state, calls `obj.save(from_admin=True, old_transaction=old_transaction)`, and — if there *was* a prior transaction (i.e., an actual state change) — **enqueues a Celery task** `CallbacksService.send_message_to_merchant.delay(obj.id)` to push the new status to the merchant's webhook. This is a critical side effect: **editing a transaction's status in admin fires an outbound merchant webhook.**
  - Custom **admin actions**:
    - `send_callbacks_to_merchants` — manually re-sends the merchant webhook for selected transactions (logs then calls the same Celery task).
    - `check_partner_status` — enqueues `TransactionCheckService.update_transaction_start.delay(transaction.id)` to reconcile status with the partner/PSP.
    - `decline_transactions` — **defined twice** (duplicate method name; the second definition silently overrides/shadows the first in Python, so only the second is ever registered — dead code alert) — transitions `status: ACCEPTED → DECLINED` only (guards on both status and, in the first/shadowed version only, `direction == 'IN'`), routes through `save_model` so it also enqueues the merchant callback, reports counts of updated/skipped/errored via `messages`.
  - `get_readonly_fields` locks down `direction`, `payment_method_company`, `merchant`, `addition_info`, `date_create/update` (plus two computed display fields) once a transaction exists — i.e. most of the transaction's identity/config is immutable post-creation; only `status`/amount-ish fields are meant to be admin-editable.
  - Heavy `list_filter` usage of third-party filter widgets: `MultiSelectRelatedFieldListFilter`, `MultiSelectFieldListFilter`, `DateRangeFilter`, `DateTimeRangeFilter` (from `django-more-admin-filters`/`django-admin-rangefilter`) — UX patterns (multi-select + date-range filters) to replicate in the FastAPI admin's filter UI.
- **AntiFraudBlockedMerchantUsers** — per-merchant end-user ban list with a "second chance" grace mechanism (`second_chance`, `second_chance_counter`, `second_chance_date`) and `permanent_ban`; `save_model` override diffs old vs. new state to auto-stamp `second_chance_date`/`ban_date` timestamps based on transitions (business logic embedded in `Model.save(from_admin=True, old_value=...)`, mirrored from a DTO built in `admin.py`).
- **Appeal** — dispute/chargeback record: O2O to `Transaction`, `status` (defaults to `APPEAL`), `description`, `type_appeal`, `photo` (`FileField`). Admin displays an inline `<img>` thumbnail of the photo. **Note: `AppealAdmin` class is defined but never `@admin.register`ed** — dead/orphaned code, flag for the team (transactions can be appealed in the model but there is no way to manage appeals from admin currently, or it's registered elsewhere not found).
- **Settlements** (`SettlementsAdmin`) — internal double-entry-like ledger movements: `settl_type` (FROM_PARTNER/TO_PARTNER/TO_MERCHANT/FROM_MERCHANT), links to `CompanyBalance` and `MerchantBalance`, `conversion_rate`, `amount_in_usdt`, `final_amount(_in_usdt)`, `wallet`, `tracker_link`, `tg_id` (Telegram user id — implies a Telegram bot integration for settlement notifications). Very complex `save()` on the model itself (see §7) delegates to `SettlementSaveService` to mutate balances and mirror a `Transaction` row.
  - Custom `TransactionSettlementAdminForm` dynamically restricts the `balance_partner`/`balance_merchant` FK querysets to the already-linked company/merchant on edit, and makes `conversion_rate` optional specifically when the merchant is flagged as a USDT "exchanger" (`UserConfig.enable_usdt_exchanger`) — cross-app conditional validation living in the admin form.
  - `get_readonly_fields`/`get_exclude` vary by `settl_type` (different field sets are editable/visible per settlement direction) — another example of admin-only conditional-UI business logic.
  - Action `send_callbacks_to_tg_user` — notifies a Telegram user via `CallbacksService.send_message_to_tg_user`.
- **BrokenRequisite** (`ImportExportModelAdmin`) — auto-tracked "bad" P2P card requisites (`failed_count`, `status` WORK/BLOCKED, `blocked_until`); model `save()` auto-sets `blocked_until = now + block_duration_hours` (from the singleton `RequisiteSettings`) when transitioning to BLOCKED, and resets `failed_count`/`blocked_until` when manually reset to WORK. Two `ModelResource`s registered: one plain export, one (`BrokenRequisiteTransactionResource`) that **overrides `export()` entirely** to instead export matching `Transaction` rows (joining on `p2p_card` prefix match) rather than `BrokenRequisite` rows — an unusual "export a different model's data from this admin" pattern. Action `unblock_requisite` bulk-resets status/failed_count/blocked_until.
- **RequisiteSettings** — singleton config row (`pk` forced to 1 in `save()`); `has_add_permission` returns `False` once one row exists; `has_delete_permission` always `False` — a **permission-override pattern for enforcing singleton config models** that FastAPI needs an equivalent guard for.

### whitelist (`whitelist/admin.py`)
- **WhitelistsMerchant** / **WhitelistsPartner** — straightforward `(entity, ip)` pairs, IPv4/IPv6-validated via a custom validator (`validate_ip4_or_ip6`), `date_hierarchy` on `date_create`. No custom actions.

### conversion_statistic (`conversion_statistic/admin.py`)
- **ConversionStatisticsNew** / **...PartnersNew** / **...MerchantNew** — three parallel funnel-metrics tables (by method / by partner-method / by merchant-method), all fully `readonly_fields` in admin (**write-only from Celery**, admin is read/export-only), each with `ImportExportModelAdmin` + a `ModelResource` for CSV/XLSX export, and a shared custom `get_search_results` override across all three that manually expands `search_fields` into `Q(...icontains=...)` — likely because the default Django admin search doesn't traverse the relation depth needed. Model `save()` computes `conversion_percent`/`conversion_percent_paid_orders` and stamps `date_only` from `date_create` — again, calculated business logic embedded in `Model.save()`.

### sandbox (`sandbox/admin.py`)
- **TestCredits** — fake requisite/`PaymentMethod` pairs with a `wanted_status_callback` (desired simulated result) and `is_active`; `requisite_details` is a `JSONField`.

### bin_checker (`bin_checker/admin.py`)
- **BanksNames** / **BinNumbers** — simple BIN→bank lookup tables, `nspk_code` = Russia's National Payment Card System bank identifier.

---

## 4. Auth & permissions model

- **`AUTH_USER_MODEL`** is **not customized** — settings.py has no `AUTH_USER_MODEL` override, so it uses **stock `django.contrib.auth.models.User`**. `personal_account_auth/models.py` even does `from django.contrib.auth.models import User` directly (in addition to `get_user_model()`), confirming no custom user model swap.
- **Merchant identity** is layered on top of the stock `User` via the `Merchant` model (FK, not OneToOne — a single Django `User` **can** own multiple `Merchant` rows, differentiated by `name`, enforced via `unique_together(user, name)`). Each `Merchant` carries its own `public_key`/`private_key` API credential pair (64/128-char hex, `secrets.token_hex`-generated) — this is the **merchant-facing API auth mechanism**, separate from admin/staff auth.
- **Admin/staff auth** is plain Django auth: `django.contrib.admin` + `django.contrib.sessions` + `django.contrib.auth` middleware, session-cookie-based login at `/{ADMIN_URL_KEY}/` (the admin mount path itself is **configurable via a secret**, `ADMIN_URL_KEY` from Vault, defaulting to `"admin"` — i.e. security-through-obscurity for the admin URL in addition to auth). `DJANGO_SUPERUSER_USERNAME`/`EMAIL`/`PASSWORD` are Vault-sourced, used presumably by a management command/bootstrap script (not found in the apps explored, likely in `manage.py`/deploy scripts) to seed the initial superuser.
- **Merchant-facing REST API auth** uses **Djoser + `rest_framework_simplejwt`** (`ACCESS_TOKEN_LIFETIME = 5 days`, Bearer scheme) plus `rest_framework.authentication.TokenAuthentication` as a fallback — both are enabled simultaneously in `REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`. `djangorestframework_simplejwt.token_blacklist` is installed (supports logout/token revocation). Custom Djoser serializers are wired (`CustomUserCreateSerializer`, `CustomUserSerializer`).
- **Permissions/groups**: No evidence of custom Django `Group`/`Permission` usage or row-level object permissions anywhere in the explored admin/model code — no `django-guardian`, no per-object permission checks beyond two admin-class-level overrides:
  - `RequisiteSettingsAdmin.has_add_permission`/`has_delete_permission` (singleton-row enforcement, not user-based).
  - `MerchantBalanceAdmin.refresh_balances` view explicitly checks `request.user.is_superuser` (superuser gate on a specific dangerous raw-SQL action) — this is the **only row/action-level permission check found** in the whole admin surface. Everything else relies on Django's default staff/superuser + model-level permission matrix (`is_staff`, `is_superuser`, and the standard add/change/delete/view permission checkboxes per model, presumably managed via Django Groups in the admin UI itself, though no code configures specific groups).
- **No existing multi-tenant/Brand/Merchant-scoping-other-data concept exists at the *admin-user* level.** `Merchant` scopes payment configuration and transactions (i.e., merchants are tenants **of the platform**, not of each other), but there is nothing that scopes *which admin staff user* can see which `Merchant`'s data — any Django staff user with model permissions sees ALL merchants/companies/transactions across the whole brand. This is an important gap: **the entire "brand_id from JWT" multi-tenancy concept the new FastAPI service needs does not exist in this codebase at all** — today, multi-tenancy across AmPay/RajaPay/quiet-forest is achieved purely by having **three separate physical databases/deployments** of the identical codebase, not by any in-app tenant column or scoping logic. The FastAPI service will be introducing brand-scoping as new functionality, not porting existing logic.

---

## 5. Secrets & config management

- **Primary mechanism: HashiCorp Vault**, via `core/vault_loader.py` using the `hvac` client:
  - `VAULT_ADDR`, `VAULT_USERNAME`, `VAULT_PASSWORD`, `VAULT_MOUNT` (default `"backend"`) read from OS env vars.
  - If `VAULT_TOKEN` env var isn't set but username/password are, it logs into Vault (`client.auth.userpass.login`) to mint a temp token.
  - Reads three KV-v2 secret paths under the mount: **`urls`**, **`settings`**, **`api_keys`** → `VAULT_URLS`, `VAULT_SETTINGS`, `VAULT_API_KEYS` dicts, which are then `globals().update(...)`'d directly into `settings.py`'s module namespace (`core/settings.py:38-40`) — i.e. **every key in those three Vault paths becomes a Django settings constant automatically**, an open-ended/implicit contract (impossible to fully enumerate secret names without live Vault access; only names explicitly referenced via `VAULT_SETTINGS.get(...)`/`VAULT_URLS.get(...)` in `settings.py` are visible statically — see below).
  - **Fallback**: if `VAULT_ADDR` is not set, falls back to a local **`.env` file** via `python-dotenv` (`load_dotenv(BASE_DIR/".env")`) — dev/local-only path.
  - At the very end of `settings.py` (line 423-424), a specific list of raw env var names (`keys_list` — `VAULT_ADDR`, `VAULT_USERNAME`, `VAULT_PASSWORD`, `VAULT_MOUNT`, `DEBUG`, `DB_HOST(_TEST)`, `NGINX_PORT*`, `POSTGRES_*`, `DB_PORT`) are explicitly **popped from `os.environ`** after being consumed — a deliberate (if partial) hardening step to reduce secret residency in the process environment post-boot.
- **Explicit secret-like settings sourced from Vault** (`VAULT_SETTINGS.get(...)` in `settings.py`), none hardcoded with real values (defaults given are placeholders like `"admin"`/`"django_secret_key"`, used only if Vault is unreachable — i.e., **the codebase has an insecure-by-default fallback** worth flagging):
  - `SECRET_KEY` (Django) — default fallback `"django_secret_key"` (weak, but only used if Vault fails).
  - `DJANGO_SUPERUSER_USERNAME` (default `"admin"`), `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_PASSWORD` (default `"admin"` — **weak default flagged**).
  - `ADMIN_URL_KEY` (default `"admin"` — admin mount path), `ADMIN_PUBLIC_KEY`, `ADMIN_PRIVATE_KEY` (purpose unclear from settings.py alone — likely an admin-level API keypair analogous to `Merchant.public_key/private_key`; not traced further).
  - Database: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (falls back to hardcoded string `"postgres"` **only** when running against `localhost`/test — flagged as a hardcoded default at `core/settings.py:50`, acceptable for local dev but worth confirming it can never leak to a real environment), `DB_HOST`, `DB_HOST_TEST`, `DB_PORT`.
  - Redis/Celery: `REDIS_HOST`, `REDIS_HOST_TEST`, `REDIS_PORT` (default `6379`), `REDIS_DB` (default `0`), `CELERY_BROKER_URL(_TEST)`, `CELERY_RESULT_BACKEND(_TEST)`, `CELERY_TASK_TRACK_STARTED`.
  - Domains/URLs: `SERVER_IP`, `HOST_ADMIN`, `DOMAIN_NAME`, `HPP_DOMEN_QF`, `IDEA_PAYMENTS_7K_DOMEN`, `N8N_USDT_RATE_FALLBACK_WEBHOOK_URL` (from `VAULT_URLS`), `BOT_CALLBACK_URL` (referenced in `api_mediator/business_logic/services/callbacks.py` via `settings.BOT_CALLBACK_URL` — Telegram-bot callback target).
  - `APP_TIMEZONE`.
  - **`VAULT_API_KEYS`** — an entire Vault KV path dedicated to (presumably per-payment-provider) API keys/secrets, injected wholesale into settings via `globals().update(VAULT_API_KEYS)`. Given ~90 payment-provider integration packages exist under `api_mediator/business_logic/api_services/`, this Vault path almost certainly holds **dozens of per-provider API keys/secrets/webhook signing secrets** not individually enumerable from `settings.py` alone — each provider's `constants.py`/`*_service.py` should be grepped for `settings.<NAME>` references to fully enumerate before the FastAPI port (not done here due to scope — 90+ provider packages).
- **No secrets found hardcoded in application code** during this pass (aside from the acknowledged local-dev-only Postgres/superuser password fallbacks noted above). No `.env` file with literal secrets was present in the read repo copy (a `.gitignore`'d `.env` may exist at runtime but wasn't included in this snapshot).
- **Django `DEBUG`** doubles as an environment switch beyond the standard meaning — it also selects which Redis host/DB host/logging config (`dev.yaml` vs `prod.yaml`) to use, so `DEBUG=True` effectively also means "test/local infrastructure," not just Django debug mode — worth knowing since a naive FastAPI port shouldn't conflate "debug logging" with "which DB to connect to."

---

## 6. Database

- **Single relational engine**: `django.db.backends.postgresql_psycopg2` (Postgres), no other engine anywhere, no Django multi-DB routers configured — `DATABASES = {"default": {...}}` only. Confirms the stated architecture: **one Postgres database per brand/monolith**, not multiple DBs within one Django instance.
- `CONN_MAX_AGE = 60` (persistent connections).
- **Redis** used for `django-redis` cache backend (`CACHES["default"]`) — used heavily by `api_mediator`'s cache-invalidation admin actions (`invalidate_cache()` in `personal_account_transaction/business_logic/services/window/cache_invalidation.py`, not fully read but clearly a Redis-backed cache of computed payment-method availability, keyed by currency/merchant).
- **Notable ORM field types / patterns for a SQLAlchemy port**:
  - `models.DecimalField` used everywhere for money — precision varies meaningfully by purpose: `(10,2)` for fiat transaction amounts, `(18,2)`/`(18,4)` for balances/statistics, `(20,8)`/`(18,8)` for USDT/crypto amounts (8 decimal places = satoshi-level precision) — **preserve these exact precisions**, don't collapse to a single Numeric type.
  - `models.JSONField` on `UserConfig.manual_usdt_rates` and `sandbox.TestCredits.requisite_details` — map to Postgres `JSONB` in SQLAlchemy.
  - `models.TextChoices` used pervasively for enums (`DirectionChoices`, `StatusChoices`, `SettlementTypeChoices`, `RequisiteStatus`, `ExchangerTypeChoices`, `ExchangerSourceChoices`, `StatisticsResultChoices`) — all stored as plain `CharField`s with app-level choice validation, **not** Postgres native `ENUM` types — straightforward to replicate as Python `Enum` + `String` column in SQLAlchemy, or upgrade to native enums if desired.
  - `Transaction` has a **partial index** (`condition=models.Q(status="SUCCESS")` on `date_create`) — Postgres-specific; SQLAlchemy needs an explicit `postgresql_where` clause to replicate.
  - Several models use `unique_together`/`UniqueConstraint` combos that encode real business rules (e.g., `PaymentMethod`: unique on `(name, currency, sub_method, direction)` AND separately on `(token, direction)`; `Transaction`: unique `(merchant_system_id, merchant)` — i.e. merchant-supplied external IDs are only unique per-merchant, not globally).
  - `db_table` is explicitly set on nearly every model (e.g. `currency`, `bank`, `card`, `company`, `payment_method`, `merchant`, `transaction`, `settlement`, `merchantbalance`) — **these physical table names are the actual stable contract to target from SQLAlchemy models**, independent of Django class names.
  - FK `to=` targets frequently use **string references across apps** (e.g. `"personal_account_auth.Merchant"`, `"api_client.Currency"`, `"api_mediator.PaymentMethodCascade"`) — confirms genuine cross-app relational coupling; the whole schema is one interconnected graph, not cleanly separable by app boundary.
  - Migrations were not deeply audited beyond confirming they exist per app (`*/migrations/0001_initial.py` etc.) and use the Postgres backend; no custom/raw-SQL migration operations were spot-checked beyond the raw-SQL admin view described below (which runs ad hoc, not via a migration).

---

## 7. Domain models relevant to a payments admin (consolidated cross-reference)

| Concern | Model(s) | Admin-exposed operations |
|---|---|---|
| **Transactions** | `Transaction` (personal_account_transaction) | View/edit/import/export; manual status change (esp. `DECLINED`) triggers merchant webhook via Celery; bulk actions: resend callback, re-check partner status, bulk-decline (ACCEPTED→DECLINED only). |
| **Merchants** | `Merchant`, `MerchantBalance`, `MerchantPaymentMethod` (personal_account_auth/api_mediator) | Create/edit merchant + API keys (auto-generated); bulk-assign payment method templates or ad hoc method sets to merchants (two custom wizard actions); manually edit merchant balances; raw-SQL "recalculate balances from transaction history" admin view (superuser-gated). |
| **Partners/PSPs ("Companies")** | `Company`, `PaymentMethodCompany`, `CompanyBalance` (api_mediator) | Configure partner's rate/limits/priority per method (list-editable grid); manage partner balances (with computed USDT-equivalent display columns); cache-invalidation actions. |
| **Payment methods / routing** | `PaymentMethod`, `PaymentMethodCascade(Item)`, `PaymentMethodTemplate`/`TemplateMethodMapping` | Define methods per currency/direction; build ordered failover cascades of partner-methods with priority validation; define reusable method templates for bulk merchant onboarding. |
| **Wallets/balances** | `MerchantBalance`, `CompanyBalance` | View/edit balances (fiat + USDT-equivalent, some fields readonly); balance-refresh raw SQL recompute action. |
| **Payouts** | Modeled implicitly as `Transaction.direction == "OUT"` — no separate "Payout" model; OUT transactions ARE payouts. |
| **KYC** | **None found.** No KYC/verification/document-upload model exists anywhere in the explored apps. |
| **API keys** | `Merchant.public_key`/`private_key` (merchant-level only); `ADMIN_PUBLIC_KEY`/`ADMIN_PRIVATE_KEY` exist as Vault settings but no corresponding Django model was found managing/rotating them — likely used as static config, not per-entity keys. |
| **Webhooks/callbacks** | No persisted "Webhook" model; `Transaction.callback_url` is a per-transaction merchant callback target; delivery is via Celery task `CallbacksService.send_message_to_merchant`, triggered from `TransactionAdmin.save_model` and the `send_callbacks_to_merchants` action. Inbound webhooks are handled per-provider via `*_callback.py` files in `api_mediator/business_logic/api_services/<provider>/` (not admin-related, presentation-layer views). |
| **Invoices** | **None found** — no Invoice model; the platform's unit of work is the `Transaction`. |
| **Disputes/chargebacks** | `Appeal` model exists (transaction + status + description + type + photo evidence) but **its `AppealAdmin` is defined and never registered** — currently unmanageable from Django admin (dead code / gap to flag). |
| **Fraud/risk** | `AntiFraudBlockedMerchantUsers` (per-merchant end-user ban list with "second chance" grace flow) and `BrokenRequisite`/`RequisiteSettings` (auto-blocking bad P2P requisites after N failures, admin action to unblock). |
| **Sandbox/testing** | `TestCredits` (sandbox app) — fake requisites with a desired simulated callback status, for merchant integration testing. |
| **Conversion / funnel analytics** | `ConversionStatisticsNew` / `...PartnersNew` / `...MerchantNew` — read-only in admin, populated by Celery/business logic elsewhere, exportable to CSV/XLSX. |
| **BIN lookup** | `BanksNames`/`BinNumbers` — simple reference data for identifying a card's issuing bank. |
| **IP allowlisting** | `WhitelistsMerchant`/`WhitelistsPartner` (new) and `personal_account_auth.WhiteList` (legacy, merchant-only) — appear to be a duplicated/overlapping concept across two apps; worth reconciling during the port rather than porting both verbatim. |

### Payment-provider integrations (`api_mediator/business_logic/api_services/`)
Roughly **90 provider sub-packages** (afrpay, airpay, aispay, antrpay, apexpay, archex(_new), argospay, bars, blackout, bnpay, bovapay, bridgepay, brusnika, buckspay, cashnode, dbc, euphoria, expay, fintrix, flexo(_new), garex, goldex, icepay, incas, jmit, marvellousbat, menu, meridian, monopolia, montra, nirvana, nodex, noname, onixcore, otc, pandapay, panem, pay404, payalma, payberry, paylonium, paymap, paymatrix, paypass, payscrow(+alfa), payshark, pixelwave, prmoney, r2s, readout, royalfinance, rubpay, sharq, spinpay, univex, and more not fully enumerated — list truncated by tool scope), each typically with `constants.py` + `<name>_service.py` (outbound API calls) + `<name>_callback.py` (inbound webhook parsing). This is **presentation/integration-layer code, not admin-panel logic** — out of scope for the admin migration itself, but the `Company`/`PaymentMethodCompany` admin models are the **configuration surface** for these integrations (rates, limits, priority, active/inactive), which IS in scope.

---

## 8. Background jobs / Celery triggered from admin actions

- `core/celery.py` configures the Celery app; `core/settings.py` defines `CELERY_BEAT_SCHEDULE` with 5 periodic (non-admin-triggered) jobs: `api_client.tasks.form_selling_info_for_available_fiats` (15 min), `api_mediator.tasks.update_payment_methods_company_daily_limits` (daily at midnight — resets the `current_daily_*` counters on `PaymentMethodCompany`), `api_mediator.tasks.check_company_balances_alerts` (every 30 min — presumably checks `CompanyBalance.alert_balance_percent`/`insurance_balance` thresholds), `api_client.tasks.update_usdt_rates` (30 sec), `api_client.tasks.update_usdt_rates_for_exchanger_users` (30 sec).
- **Admin-triggered Celery tasks** (the ones relevant to the FastAPI port, since the new admin service will need to either replicate this async dispatch or trigger it via message queue / RPC into the existing monolith's worker):
  - `CallbacksService.send_message_to_merchant.delay(transaction_id)` — fired from `TransactionAdmin.save_model` (automatically, whenever an edited transaction previously existed) and from the explicit `send_callbacks_to_merchants` bulk action.
  - `TransactionCheckService.update_transaction_start.delay(transaction_id)` — fired from the `check_partner_status` bulk action, to reconcile a transaction's status against the partner/PSP.
  - `CallbacksService.send_message_to_tg_user(settlement_id)` — fired (not clear if `.delay()` — the call site in `SettlementsAdmin.send_callbacks_to_tg_user` does **not** show `.delay(...)`, calling it directly; worth double-checking whether this is actually async or a bug/sync call at `personal_account_transaction/admin.py:814`).

---

## 9. Dependencies relevant to admin/auth/DRF/secrets (from `pyproject.toml`)

```
django==4.2.3
djangorestframework==3.14.0
djangorestframework-simplejwt==5.2.2
djoser==2.2.3
drf-spectacular==0.27.0
django-admin-multi-select-filter==1.3.0
django-admin-rangefilter==0.11.2
django-appconf==1.0.6
django-cors-headers==4.3.1
django-csp==4.0
django-health-check>=3.20.0
django-import-export==3.3.3
django-ipware==7.0.1
django-more-admin-filters==1.7
django-otp==1.2.2                 # installed but not seen wired into INSTALLED_APPS/urls in this pass — verify if 2FA is actually active for admin
django-redis==5.4.0
django-select2==8.1.2
django-templated-mail==1.1.1
social-auth-app-django==5.4.1     # installed but no usage found in explored apps — verify if any SSO/OAuth admin login exists
social-auth-core==4.5.4
hvac==2.3.0                       # Vault client
psycopg2-binary==2.9.9
celery==5.3.6
redis==5.0.1
pyjwt==2.8.0
pycryptodome==3.20.0
cryptography==42.0.8
python-dotenv==1.0.0
tablib==3.5.0 / openpyxl / xlrd / xlwt / odfpy / markuppy / defusedxml  # import_export's format backends
iso4217==1.11.20220401
```
`django-otp` and `social-auth-app-django`/`social-auth-core` being present in dependencies but not observed wired up anywhere in the explored settings/urls/apps is worth a follow-up grep across `lk/` and `core/urls.py` before assuming admin auth is purely session+password — if OTP or social login IS active for admin staff, that's an auth requirement the FastAPI service must also replicate.

---

## 10. Other architecturally notable points for the FastAPI rebuild

1. **Business logic lives inside `admin.py` and `Model.save()`, not in a clean service layer, in several hot paths.** Notably: `CompanyBalance`/`MerchantBalance` USDT-equivalent computation (admin display methods doing live currency conversion via `SellingInfo` lookups), the raw-SQL balance-reconciliation view, and conditional field visibility/readonly logic in `SettlementsAdmin`/`PaymentMethodCascadeAdmin`. These need to be extracted into explicit, testable service functions when ported — they cannot be "just call the Django ORM" in FastAPI.
2. **`Model.save()` methods carry the real business rules for cross-model consistency** (e.g., `Transaction.save(from_admin=True, ...)` builds a DTO and calls `TransactionSaveService.pre_save_from_admin_transaction`; `Settlements.save()` mutates the linked `MerchantBalance`/`CompanyBalance` and creates/updates a mirrored `Transaction` row as a side effect of saving a settlement). **Editing a `Settlements` row in Django admin can silently create or mutate a `Transaction` row** — this coupling must be made explicit (and probably split into distinct API operations) in the new service, not hidden inside an ORM save hook.
3. **Admin-triggered webhook delivery**: editing/declining a `Transaction` in admin fires a real outbound webhook call to the merchant (via Celery). The FastAPI admin service will need either (a) direct access to the same Celery broker/task signatures to enqueue equivalent jobs, or (b) an RPC/HTTP bridge into the existing monolith to trigger these side effects, since a rewrite that talks only to the DB would silently break merchant callback delivery.
4. **Redis cache invalidation is a first-class admin concern** (`clear_cache_by_currency`, `clear_cache_by_merchant`, `clear_all_payment_methods_cache` actions) — the new service needs to either share the same Redis cache-key scheme or provide equivalent invalidation hooks, otherwise stale payment-method availability could be served to merchants after an admin change.
5. **Dead/inconsistent code found during this pass** (worth flagging to the source team, not necessarily porting as-is):
   - `TransactionAdmin.decline_transactions` is defined **twice** in the same class — the second definition silently wins; the first (which also checked `direction == 'IN'`) is unreachable.
   - `AppealAdmin` is defined but **never registered** (`@admin.register(Appeal)` is missing) — disputes/appeals are currently unmanageable via Django admin.
   - `CompanyAdmin`'s docstring/comment claims registration was deliberately removed "so nobody accidentally changes something," but the class **is** registered with a full editable list — comment is stale/misleading, should be resolved with the source team about intended access level for `Company`.
   - `personal_account_auth.WhiteList` vs. `whitelist.WhitelistsMerchant`/`WhitelistsPartner` appear to duplicate the same IP-allowlisting concept across two apps.
6. **Two-step "wizard" admin actions** (`apply_template_to_merchants`, `apply_selected_methods_to_merchants` on `MerchantAdmin`) render an intermediate HTML form/template before executing — these are not simple one-click bulk actions and need explicit multi-step API design (e.g., a "preview" endpoint + a "confirm" endpoint) in FastAPI rather than a single POST action.
7. **Complex inline-formset validation** on `PaymentMethodCascadeItemInline` (unique priority per cascade, payment-method consistency) is currently enforced only at the Django form/formset level (`ValidatedFormSet.clean()`), not at the DB constraint level beyond `unique_together(cascade, payment_method_company)` — the priority-uniqueness rule specifically has **no DB constraint**, only admin-form validation, so it must be explicitly re-implemented as an API-level validation rule (not assumed to be DB-enforced).
8. **Referer-URL parsing for context** — `PaymentMethodCompanyAdmin.get_search_results` parses `HTTP_REFERER` with a regex to infer which cascade is being edited, to scope an autocomplete dropdown. This is a fragile Django-admin-specific UX hack (relying on Django's own generated URLs) that has no direct equivalent in a stateless API and should be replaced by an explicit query parameter contract instead.
9. **No signals (`post_save`/`pre_save` via `@receiver`) were found** in the explored apps — the "hook" pattern used throughout is instead direct `Model.save(*, from_admin=..., old_x=...)` kwargs threaded manually from `admin.py`'s `save_model`, i.e., admin-only side effects rather than global signals. This is actually good news for the port: side effects are localized to admin save paths rather than scattered via signal receivers, but it also means **the FastAPI service must reimplement each `save_model` override's logic explicitly** rather than relying on the ORM firing signals automatically.
10. **Custom middleware**: `api_client.presentation.middleware.AjaxMiddleware` and `RequestMiddleware`, plus `django-csp`'s `CSPMiddleware` — not deeply explored (outside admin `models.py`/`admin.py` scope) but worth a follow-up read since `RequestMiddleware` in particular often stashes request-scoped context (e.g., current user/IP) used elsewhere.
11. **IP-based access control precedence list** (`IPWARE_META_PRECEDENCE_ORDER`) is extensive (12+ header names, CloudFlare/Azure/Rackspace/Fastly-aware) — if the admin panel or any of it relies on real client IP for allowlisting (`whitelist`/`WhiteList` apps), the FastAPI service sitting behind whatever proxy chain exists needs the equivalent trusted-header precedence configured, not just `X-Forwarded-For`.
12. **Health checks**: `health_check` + `health_check.db`/`cache`/`storage`/`migrations`/`celery`/`celery_ping`/`db_heartbeat` are all installed — the FastAPI service should expose equivalent liveness/readiness signals per brand DB/broker if it wants operational parity.
