"""HashiCorp Vault client wrapper.

Mirrors the secrets-management principle used by the three source monoliths
(AmPay / RajaPay / quiet-forest): HashiCorp Vault (KV v2) is the source of
truth for every secret, with a `.env` fallback for local development only.

Unlike the monoliths, this service does NOT fall back to hardcoded literal
defaults when a secret is missing (see the migration research reports:
`SECRET_KEY`, `DJANGO_SUPERUSER_PASSWORD`, and a bearer token were all found
hardcoded as fallbacks in the source settings.py files). Missing required
secrets must fail startup, not silently degrade to a weak default.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

import hvac

from app.core.settings_env import env_settings

logger = logging.getLogger(__name__)


class VaultConfigError(RuntimeError):
    """Raised when a required secret cannot be resolved from Vault or env."""


class VaultClient:
    def __init__(self) -> None:
        self._client: hvac.Client | None = None
        self._cache: dict[str, dict[str, Any]] = {}

    def _get_client(self) -> hvac.Client:
        if self._client is not None:
            return self._client
        if not env_settings.vault_addr:
            raise VaultConfigError(
                "VAULT_ADDR is not set. This service requires Vault in any "
                "non-local environment; for local development set "
                "USE_LOCAL_ENV_SECRETS=true instead."
            )
        client = hvac.Client(url=env_settings.vault_addr)
        if env_settings.vault_token:
            client.token = env_settings.vault_token
        elif env_settings.vault_role_id and env_settings.vault_secret_id:
            resp = client.auth.approle.login(
                role_id=env_settings.vault_role_id,
                secret_id=env_settings.vault_secret_id,
            )
            client.token = resp["auth"]["client_token"]
        else:
            raise VaultConfigError(
                "No Vault auth method configured: set VAULT_TOKEN or "
                "VAULT_ROLE_ID/VAULT_SECRET_ID (AppRole)."
            )
        if not client.is_authenticated():
            raise VaultConfigError("Vault authentication failed.")
        self._client = client
        return client

    def read_kv(self, mount_point: str, path: str) -> dict[str, Any]:
        """Read a KV-v2 secret, cached per (mount, path) for process lifetime."""
        cache_key = f"{mount_point}:{path}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        client = self._get_client()
        try:
            resp = client.secrets.kv.v2.read_secret_version(
                mount_point=mount_point, path=path, raise_on_deleted_version=True
            )
        except Exception as exc:
            raise VaultConfigError(f"Failed reading vault secret {mount_point}/{path}: {exc}") from exc
        data = resp["data"]["data"]
        self._cache[cache_key] = data
        return data

    def get_required(self, mount_point: str, path: str, key: str) -> str:
        data = self.read_kv(mount_point, path)
        if key not in data or data[key] in (None, ""):
            raise VaultConfigError(f"Required secret '{key}' missing at {mount_point}/{path}.")
        return str(data[key])

    def get_optional(self, mount_point: str, path: str, key: str, default: str | None = None) -> str | None:
        try:
            data = self.read_kv(mount_point, path)
        except VaultConfigError:
            return default
        value = data.get(key)
        return str(value) if value not in (None, "") else default


@functools.lru_cache
def get_vault_client() -> VaultClient:
    return VaultClient()
