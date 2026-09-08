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
    # config resolution for everything else (chicken-and-egg: the control-plane
    # DB itself isn't a "brand" secret). Treat it as infra config, not a brand secret.
    control_plane_database_url: str = (
        "postgresql+asyncpg://admin_panel:admin_panel@localhost:5442/admin_panel_control"
    )

    # Single hardcoded superuser, checked in `auth_service.login` before ever
    # touching the control-plane DB — no `admin_user` row needs to exist for
    # this identity (see `HARDCODED_SUPERUSER_ID`). Deliberate simplification:
    # there is currently exactly one operator of this service, so seeding/
    # maintaining a DB-backed account for them is pure ceremony. Real
    # DB-backed accounts (`bootstrap_admin_user.py`, `AdminUser`/`BrandAccess`)
    # still work unchanged for anyone else added later. Override both in
    # production — these defaults are for local dev only.
    hardcoded_superuser_email: str = "admin@example.com"
    hardcoded_superuser_password: str = "SuperSecret123!"

    cors_allow_origins: str = "http://localhost:3000"


env_settings = EnvSettings()
