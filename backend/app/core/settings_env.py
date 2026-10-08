"""Bootstrap-level environment settings.

Only the values needed to reach Vault (or, for local dev, to skip it) live
here. Everything else — DB credentials, JWT signing key, per-brand config —
is resolved through `app.core.vault.VaultClient` at startup, never read
directly from `os.environ` outside this bootstrap layer.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Set to true only for local development. When true, secrets are read
    # from environment variables / `.env` instead of Vault. Must be false
    # (default) in any deployed environment.
    use_local_env_secrets: bool = False

    # Same variables as the monoliths' core/vault_loader.py.
    vault_addr: str | None = None
    vault_username: str | None = None
    vault_password: str | None = None
    vault_token: str | None = None  # optional alternative to username/password
    vault_mount: str = "backend"
    # Optional: where THIS service's own secrets (JWT key) live; defaults to vault_mount.
    vault_mount_admin_panel: str | None = None

    # Staff login throttling (counters live in Redis; see app.services.login_throttle).
    login_max_failed_attempts: int = 5
    login_lockout_minutes: int = 15

    # Comma-separated browser origins allowed to call the API (the frontend's public URL). No default:
    # a missing value must stop startup rather than silently allow a development origin.
    cors_allow_origins: str


env_settings = EnvSettings()
