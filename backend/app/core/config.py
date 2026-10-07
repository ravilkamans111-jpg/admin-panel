"""Secret & per-brand config resolution.

Two providers, selected by `EnvSettings.use_local_env_secrets`:

- Vault (default, required outside local dev) — laid out like the three
  monoliths' `core/vault_loader.py`: log in with VAULT_USERNAME/VAULT_PASSWORD
  (userpass) or VAULT_TOKEN, read the KV-v2 mount VAULT_MOUNT (default
  "backend") paths `api_keys`, `settings`, `urls`, and look values up by the
  monoliths' own key names. A key is tried brand-suffixed first and then plain,
  so both naming styles work from one place:

      what we need          keys tried (brand AMPAY shown)
      DB host               HOST_AMPAY, DB_HOST_AMPAY, HOST, DB_HOST
      DB port / name        DB_PORT[_AMPAY] (default 5432), POSTGRES_DB[_AMPAY]
      DB user / password    POSTGRES_USER[_AMPAY], POSTGRES_PASSWORD[_AMPAY]
      Redis                 REDIS_URL, or REDIS_HOST + REDIS_PORT + REDIS_DB [_AMPAY]
      Celery broker         CELERY_BROKER_URL[_AMPAY], CELERY_RESULT_BACKEND[_AMPAY]
      admin public key      ADMIN_PUBLIC_KEY[_AMPAY]
      timezone              APP_TIMEZONE[_AMPAY]           (default UTC)
      bot callback URL      BOT_CALLBACK_URL[_AMPAY]       (optional)
      JWT (this service)    JWT_SECRET_KEY, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES,
                            REFRESH_TOKEN_EXPIRE_DAYS      (path `admin_panel` or `settings`)

  Brand suffixes: AMPAY, RAJAPAY, QUIET_FOREST. A brand with its own mount sets
  `VAULT_MOUNT_<BRAND>`.

- Local env (.env), for development only: `BRAND_<BRAND_ID>_DB_*` variables.

No hardcoded fallback secrets exist anywhere in this module by design: a
missing required secret raises and the service fails to start, rather than
silently running with a weak default.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass

from app.core.settings_env import env_settings
from app.core.vault import VaultConfigError, get_vault_client


def _brand_key(brand_id: str) -> str:
    return brand_id.upper().replace("-", "_")


def _brand_vault_data(brand_id: str) -> dict:
    """The brand's merged Vault secrets (`api_keys` + `settings` + `urls`).

    One shared mount (`VAULT_MOUNT`, default "backend") by default; if a brand
    keeps its own mount — as each monolith does — set `VAULT_MOUNT_<BRAND>`
    (e.g. `VAULT_MOUNT_QUIET_FOREST`)."""
    mount = os.environ.get(f"VAULT_MOUNT_{_brand_key(brand_id)}") or env_settings.vault_mount
    return get_vault_client().read_merged(mount)


def _pick(data: dict, brand_id: str, *names: str, default: str | None = None) -> str | None:
    """First non-empty value for any of `names`, trying the brand-suffixed key
    first (`HOST_AMPAY` — AmPay/RajaPay style) and then the plain key (`DB_HOST`
    — quiet-forest style), so the key names of all three monoliths just work."""
    suffix = _brand_key(brand_id)
    for key in [f"{n}_{suffix}" for n in names] + list(names):
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _require(value: str | None, brand_id: str, *names: str) -> str:
    if value is None:
        raise VaultConfigError(
            f"Vault has no value for {brand_id}: expected one of "
            f"{', '.join(f'{n}_{_brand_key(brand_id)}' for n in names)} or {', '.join(names)}."
        )
    return value


@dataclass(frozen=True, slots=True)
class BrandDbConfig:
    brand_id: str
    host: str
    port: int
    database: str
    user: str
    password: str

    @property
    def async_dsn(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True, slots=True)
class BrandRedisConfig:
    """Connection to the SAME Redis instance the brand's Django monolith uses
    for its method-lookup cache (`cache_invalidation.invalidate_cache`).

    This service only ever calls `invalidate_cache`-equivalent operations —
    it never reads that cache — so a real connection here means an admin
    edit actually busts the same cache keys the monolith's Celery workers
    and web processes read from.
    """

    brand_id: str
    url: str


@dataclass(frozen=True, slots=True)
class BrandCeleryConfig:
    """Connection to the SAME Celery broker the brand's Django monolith uses.

    This service enqueues tasks by name (matching the monolith's registered
    `@shared_task` names exactly) so the monolith's own Celery workers pick
    them up and execute them — this service does not run a Celery worker
    itself, only a producer.
    """

    brand_id: str
    broker_url: str
    result_backend: str | None


@dataclass(frozen=True, slots=True)
class AuthSecrets:
    jwt_secret_key: str
    jwt_algorithm: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise VaultConfigError(
            f"USE_LOCAL_ENV_SECRETS=true but required env var {name} is not set."
        )
    return value


def get_brand_db_config(brand_id: str) -> BrandDbConfig:
    brand_key = brand_id.upper().replace("-", "_")
    if env_settings.use_local_env_secrets:
        return BrandDbConfig(
            brand_id=brand_id,
            host=_require_env(f"BRAND_{brand_key}_DB_HOST"),
            port=int(os.environ.get(f"BRAND_{brand_key}_DB_PORT", "5432")),
            database=_require_env(f"BRAND_{brand_key}_DB_NAME"),
            user=_require_env(f"BRAND_{brand_key}_DB_USER"),
            password=_require_env(f"BRAND_{brand_key}_DB_PASSWORD"),
        )
    data = _brand_vault_data(brand_id)
    return BrandDbConfig(
        brand_id=brand_id,
        host=_require(_pick(data, brand_id, "HOST", "DB_HOST"), brand_id, "HOST", "DB_HOST"),
        port=int(_pick(data, brand_id, "DB_PORT", default="5432") or 5432),
        database=_require(_pick(data, brand_id, "POSTGRES_DB"), brand_id, "POSTGRES_DB"),
        user=_require(_pick(data, brand_id, "POSTGRES_USER"), brand_id, "POSTGRES_USER"),
        password=_require(_pick(data, brand_id, "POSTGRES_PASSWORD"), brand_id, "POSTGRES_PASSWORD"),
    )


def get_brand_admin_public_key(brand_id: str) -> str | None:
    """Mirrors the source's `getattr(settings, 'ADMIN_PUBLIC_KEY', None)` —
    the special merchant public key that routes a Transaction save through
    `update_company_balance_throw_settlement` instead of the general path.
    Optional by design (source defaults to `None` when unset, not an error)."""
    brand_key = brand_id.upper().replace("-", "_")
    if env_settings.use_local_env_secrets:
        return os.environ.get(f"BRAND_{brand_key}_ADMIN_PUBLIC_KEY")
    return _pick(_brand_vault_data(brand_id), brand_id, "ADMIN_PUBLIC_KEY")


def get_brand_timezone(brand_id: str) -> str:
    """Mirrors the source's `settings.TIME_ZONE` (Vault `APP_TIMEZONE`) — decides
    which calendar day a transaction's conversion statistics belong to.
    Defaults to UTC when a brand doesn't configure one."""
    brand_key = brand_id.upper().replace("-", "_")
    if env_settings.use_local_env_secrets:
        return os.environ.get(f"BRAND_{brand_key}_TIMEZONE", "UTC")
    return _pick(_brand_vault_data(brand_id), brand_id, "APP_TIMEZONE", default="UTC") or "UTC"


def get_brand_bot_callback_url(brand_id: str) -> str | None:
    """Mirrors the source's `settings.BOT_CALLBACK_URL` — where
    `CallbacksService.send_message_to_tg_user` posts a Settlement's payload
    for the Telegram bot to deliver to the client. Optional by design: a
    brand without a bot integration configured just can't use the "Отправить
    коллбэки выбранным пользователям в телеграмм" admin action, same as
    source would fail loudly per-call rather than at startup."""
    brand_key = brand_id.upper().replace("-", "_")
    if env_settings.use_local_env_secrets:
        return os.environ.get(f"BRAND_{brand_key}_BOT_CALLBACK_URL")
    return _pick(_brand_vault_data(brand_id), brand_id, "BOT_CALLBACK_URL")


def get_brand_redis_config(brand_id: str) -> BrandRedisConfig:
    brand_key = brand_id.upper().replace("-", "_")
    if env_settings.use_local_env_secrets:
        return BrandRedisConfig(brand_id=brand_id, url=_require_env(f"BRAND_{brand_key}_REDIS_URL"))
    data = _brand_vault_data(brand_id)
    url = _pick(data, brand_id, "REDIS_URL")
    if url is None:
        host = _require(_pick(data, brand_id, "REDIS_HOST"), brand_id, "REDIS_HOST", "REDIS_URL")
        if "://" in host:
            url = host
        else:
            port = _pick(data, brand_id, "REDIS_PORT", default="6379")
            db = _pick(data, brand_id, "REDIS_DB", default="0")
            url = f"redis://{host}:{port}/{db}"
    return BrandRedisConfig(brand_id=brand_id, url=url)


def get_brand_celery_config(brand_id: str) -> BrandCeleryConfig:
    brand_key = brand_id.upper().replace("-", "_")
    if env_settings.use_local_env_secrets:
        return BrandCeleryConfig(
            brand_id=brand_id,
            broker_url=_require_env(f"BRAND_{brand_key}_CELERY_BROKER_URL"),
            result_backend=os.environ.get(f"BRAND_{brand_key}_CELERY_RESULT_BACKEND"),
        )
    data = _brand_vault_data(brand_id)
    return BrandCeleryConfig(
        brand_id=brand_id,
        broker_url=_require(_pick(data, brand_id, "CELERY_BROKER_URL"), brand_id, "CELERY_BROKER_URL"),
        result_backend=_pick(data, brand_id, "CELERY_RESULT_BACKEND"),
    )


MIN_JWT_SECRET_LENGTH = 32


def _validated_jwt_secret(secret: str) -> str:
    if len(secret) < MIN_JWT_SECRET_LENGTH or any(w in secret.lower() for w in ("change-me", "dev-only", "changeme")):
        raise RuntimeError(
            f"JWT signing key must be at least {MIN_JWT_SECRET_LENGTH} random characters and not a placeholder"
        )
    return secret


@functools.lru_cache
def get_auth_secrets() -> AuthSecrets:
    if env_settings.use_local_env_secrets:
        return AuthSecrets(
            jwt_secret_key=_require_env("JWT_SECRET_KEY"),
            jwt_algorithm=os.environ.get("JWT_ALGORITHM", "HS256"),
            access_token_expire_minutes=int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "15")),
            refresh_token_expire_days=int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "7")),
        )
    data = get_vault_client().read_merged(env_settings.vault_mount_admin_panel or env_settings.vault_mount)
    secret = data.get("JWT_SECRET_KEY")
    if secret in (None, ""):
        raise VaultConfigError("Vault has no JWT_SECRET_KEY (put it in the `admin_panel` or `settings` path).")
    return AuthSecrets(
        jwt_secret_key=_validated_jwt_secret(str(secret)),
        jwt_algorithm=str(data.get("JWT_ALGORITHM") or "HS256"),
        access_token_expire_minutes=int(data.get("ACCESS_TOKEN_EXPIRE_MINUTES") or 15),
        refresh_token_expire_days=int(data.get("REFRESH_TOKEN_EXPIRE_DAYS") or 7),
    )
