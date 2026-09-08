"""Secret & per-brand config resolution.

Two providers, selected by `EnvSettings.use_local_env_secrets`:

- Vault (default, required outside local dev): each brand's DB credentials
  live at KV path `brand-config/data/<brand_id>/db`; global service secrets
  (JWT signing key, control-plane admin bootstrap) live at
  `admin-panel/data/auth`. Deliberately namespaced by brand_id as a *path*
  segment (not a suffixed key like `DB_PASSWORD_RAJAPAY`) — the migration
  research on the three source monoliths found real cross-brand leakage
  caused by exactly that suffixed-key convention (e.g. RajaPay's settings.py
  hardcoding `AMPAY_EMAIL = COMPANY_EMAIL`, silently returning RajaPay's own
  value under AmPay's name). Path-segmented secrets make that class of bug
  structurally impossible: there is no shared namespace to alias into.

- Local env (.env), for development only: `BRAND_<BRAND_ID>_DB_*` variables.

No hardcoded fallback secrets exist anywhere in this module by design: a
missing required secret raises and the service fails to start, rather than
silently running with a weak default (see VaultConfigError in app.core.vault
and the migration reports flagging exactly this failure mode in the source
Django settings.py files).
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass

from app.core.settings_env import env_settings
from app.core.vault import VaultConfigError, get_vault_client

BRAND_CONFIG_MOUNT = "brand-config"
ADMIN_PANEL_MOUNT = "admin-panel"


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
    vault = get_vault_client()
    data = vault.read_kv(BRAND_CONFIG_MOUNT, f"{brand_id}/db")
    required = ("host", "port", "database", "user", "password")
    missing = [k for k in required if k not in data or data[k] in (None, "")]
    if missing:
        raise VaultConfigError(
            f"Vault secret {BRAND_CONFIG_MOUNT}/{brand_id}/db missing keys: {missing}"
        )
    return BrandDbConfig(
        brand_id=brand_id,
        host=str(data["host"]),
        port=int(data["port"]),
        database=str(data["database"]),
        user=str(data["user"]),
        password=str(data["password"]),
    )


def get_brand_admin_public_key(brand_id: str) -> str | None:
    """Mirrors the source's `getattr(settings, 'ADMIN_PUBLIC_KEY', None)` —
    the special merchant public key that routes a Transaction save through
    `update_company_balance_throw_settlement` instead of the general path.
    Optional by design (source defaults to `None` when unset, not an error)."""
    brand_key = brand_id.upper().replace("-", "_")
    if env_settings.use_local_env_secrets:
        return os.environ.get(f"BRAND_{brand_key}_ADMIN_PUBLIC_KEY")
    vault = get_vault_client()
    return vault.get_optional(BRAND_CONFIG_MOUNT, f"{brand_id}/transaction_admin", "admin_public_key")


def get_brand_redis_config(brand_id: str) -> BrandRedisConfig:
    brand_key = brand_id.upper().replace("-", "_")
    if env_settings.use_local_env_secrets:
        return BrandRedisConfig(brand_id=brand_id, url=_require_env(f"BRAND_{brand_key}_REDIS_URL"))
    vault = get_vault_client()
    url = vault.get_required(BRAND_CONFIG_MOUNT, f"{brand_id}/redis", "url")
    return BrandRedisConfig(brand_id=brand_id, url=url)


def get_brand_celery_config(brand_id: str) -> BrandCeleryConfig:
    brand_key = brand_id.upper().replace("-", "_")
    if env_settings.use_local_env_secrets:
        return BrandCeleryConfig(
            brand_id=brand_id,
            broker_url=_require_env(f"BRAND_{brand_key}_CELERY_BROKER_URL"),
            result_backend=os.environ.get(f"BRAND_{brand_key}_CELERY_RESULT_BACKEND"),
        )
    vault = get_vault_client()
    broker_url = vault.get_required(BRAND_CONFIG_MOUNT, f"{brand_id}/celery", "broker_url")
    result_backend = vault.get_optional(BRAND_CONFIG_MOUNT, f"{brand_id}/celery", "result_backend")
    return BrandCeleryConfig(brand_id=brand_id, broker_url=broker_url, result_backend=result_backend)


@functools.lru_cache
def get_auth_secrets() -> AuthSecrets:
    if env_settings.use_local_env_secrets:
        return AuthSecrets(
            jwt_secret_key=_require_env("JWT_SECRET_KEY"),
            jwt_algorithm=os.environ.get("JWT_ALGORITHM", "HS256"),
            access_token_expire_minutes=int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "15")),
            refresh_token_expire_days=int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "7")),
        )
    vault = get_vault_client()
    return AuthSecrets(
        jwt_secret_key=vault.get_required(ADMIN_PANEL_MOUNT, "auth", "jwt_secret_key"),
        jwt_algorithm=vault.get_optional(ADMIN_PANEL_MOUNT, "auth", "jwt_algorithm", "HS256") or "HS256",
        access_token_expire_minutes=int(
            vault.get_optional(ADMIN_PANEL_MOUNT, "auth", "access_token_expire_minutes", "15") or 15
        ),
        refresh_token_expire_days=int(
            vault.get_optional(ADMIN_PANEL_MOUNT, "auth", "refresh_token_expire_days", "7") or 7
        ),
    )
