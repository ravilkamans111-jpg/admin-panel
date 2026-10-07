"""HashiCorp Vault access, wired the same way as the three source monoliths
(`core/vault_loader.py` in AmPay / RajaPay / quiet-forest).

Same environment variables, same login, same layout — so going to a real
environment is "put the same values in":

    VAULT_ADDR       Vault URL
    VAULT_USERNAME   userpass login            (or VAULT_TOKEN instead)
    VAULT_PASSWORD
    VAULT_MOUNT      KV v2 mount, default "backend"

Secrets live at the mount's `settings`, `urls` and `api_keys` paths as flat
UPPER_CASE keys (`POSTGRES_PASSWORD`, `REDIS_HOST_AMPAY`, ...); the three are
merged into one dict exactly like the monoliths' `globals().update(...)`.

Unlike the monoliths, a missing required secret fails loudly instead of
falling back to a hardcoded literal (their settings.py files default
`SECRET_KEY`, the Django superuser password and a bearer token).
"""

from __future__ import annotations

import functools
import logging
from typing import Any

import hvac

from app.core.settings_env import env_settings

logger = logging.getLogger(__name__)

# Monolith's `vault_loader`: VAULT_URLS_PATH / VAULT_SETTINGS_PATH / VAULT_API_KEYS_PATH.
SECRET_PATHS = ("api_keys", "settings", "urls", "admin_panel")  # later wins; first three are the monoliths' paths, `admin_panel` holds this service's own (JWT) keys


class VaultConfigError(RuntimeError):
    """Raised when a required secret cannot be resolved from Vault or env."""


class VaultClient:
    def __init__(self) -> None:
        self._client: hvac.Client | None = None
        self._cache: dict[str, dict[str, Any]] = {}
        self._missing: set[str] = set()

    def _login(self) -> hvac.Client:
        if not env_settings.vault_addr:
            raise VaultConfigError(
                "VAULT_ADDR is not set. This service requires Vault in any non-local "
                "environment; for local development set USE_LOCAL_ENV_SECRETS=true instead."
            )
        client = hvac.Client(url=env_settings.vault_addr)
        if env_settings.vault_token:
            client.token = env_settings.vault_token
        elif env_settings.vault_username and env_settings.vault_password:
            try:
                resp = client.auth.userpass.login(
                    username=env_settings.vault_username, password=env_settings.vault_password
                )
            except Exception as exc:
                raise VaultConfigError(f"Vault userpass login failed: {exc}") from exc
            client.token = resp["auth"]["client_token"]
        else:
            raise VaultConfigError("No Vault credentials: set VAULT_USERNAME + VAULT_PASSWORD (or VAULT_TOKEN).")
        if not client.is_authenticated():
            raise VaultConfigError("Vault authentication failed.")
        return client

    def _get_client(self) -> hvac.Client:
        if self._client is None:
            self._client = self._login()
        return self._client

    def read_kv(self, mount_point: str, path: str) -> dict[str, Any]:
        """Reads one KV-v2 secret, cached per (mount, path) for the process
        lifetime. A userpass token is short-lived, so on a permission error the
        client logs in again once and retries before giving up."""
        cache_key = f"{mount_point}:{path}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        for attempt in (1, 2):
            client = self._get_client()
            try:
                resp = client.secrets.kv.v2.read_secret_version(
                    mount_point=mount_point, path=path, raise_on_deleted_version=True
                )
                break
            except hvac.exceptions.Forbidden as exc:
                if attempt == 2 or env_settings.vault_token:
                    raise VaultConfigError(f"Vault denied access to {mount_point}/{path}: {exc}") from exc
                self._client = None  # token expired/revoked — log in again
            except Exception as exc:
                raise VaultConfigError(f"Failed reading vault secret {mount_point}/{path}: {exc}") from exc
        data = resp["data"]["data"]
        self._cache[cache_key] = data
        return data

    def read_merged(self, mount_point: str) -> dict[str, Any]:
        """`api_keys` + `settings` + `urls` of one mount, merged like the
        monoliths do. A path that doesn't exist is skipped, not fatal."""
        merged: dict[str, Any] = {}
        found_any = False
        for path in SECRET_PATHS:
            key = f"{mount_point}:{path}"
            if key in self._missing:
                continue
            try:
                merged.update(self.read_kv(mount_point, path))
                found_any = True
            except VaultConfigError as exc:
                # A mount needn't have all four paths (e.g. no `api_keys`); remember and stay quiet.
                self._missing.add(key)
                logger.info("Vault %s/%s not readable, skipping: %s", mount_point, path, exc)
        if not found_any:
            raise VaultConfigError(f"No readable secrets under Vault mount '{mount_point}' ({'/'.join(SECRET_PATHS)}).")
        return merged


@functools.lru_cache
def get_vault_client() -> VaultClient:
    return VaultClient()
