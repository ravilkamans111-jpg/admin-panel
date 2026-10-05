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

    vault_addr: str | None = None
    vault_token: str | None = None
    vault_role_id: str | None = None
    vault_secret_id: str | None = None

    # Control-plane DB (staff auth / brand registry / audit log) connection.
    # Sourced from env even in prod, since it is needed to bootstrap Vault-based
    # config resolution for everything else. No default: a missing value must
    # fail startup rather than silently pointing at a development database.
    control_plane_database_url: str

    # Staff login policy.
    login_max_failed_attempts: int = 5
    login_lockout_minutes: int = 15
    min_password_length: int = 12

    cors_allow_origins: str = "http://localhost:3000"


env_settings = EnvSettings()
