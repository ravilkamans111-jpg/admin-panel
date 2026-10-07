"""Vault-backed config resolution, laid out like the monoliths' vault_loader:
flat UPPER_CASE keys, brand-suffixed (AmPay/RajaPay) or plain (quiet-forest)."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("USE_LOCAL_ENV_SECRETS", "true")

from app.core import config
from app.core.settings_env import env_settings
from app.core.vault import VaultClient, VaultConfigError

AMPAY_STYLE = {
    "HOST_AMPAY": "db.ampay.internal", "DB_PORT": "5433", "POSTGRES_DB": "ampay_db",
    "POSTGRES_USER": "ampay_user", "POSTGRES_PASSWORD": "s3cret",
    "REDIS_HOST_AMPAY": "redis.ampay.internal", "REDIS_PORT": "6380", "REDIS_DB": "2",
    "CELERY_BROKER_URL_AMPAY": "redis://broker.ampay:6379/1",
    "CELERY_RESULT_BACKEND_AMPAY": "redis://broker.ampay:6379/3",
    "ADMIN_PUBLIC_KEY": "pk_admin", "APP_TIMEZONE": "Europe/Moscow", "BOT_CALLBACK_URL": "https://bot/cb",
}
QF_STYLE = {
    "DB_HOST": "db.qf.internal", "POSTGRES_DB": "qf", "POSTGRES_USER": "qf", "POSTGRES_PASSWORD": "pw",
    "REDIS_HOST": "redis.qf", "CELERY_BROKER_URL": "redis://broker.qf:6379/0",
}


class FakeVault:
    def __init__(self, per_mount: dict[str, dict]):
        self.per_mount = per_mount
        self.mounts_read: list[str] = []

    def read_merged(self, mount: str) -> dict:
        self.mounts_read.append(mount)
        if mount not in self.per_mount:
            raise VaultConfigError(f"no mount {mount}")
        return self.per_mount[mount]


@pytest.fixture
def vault(monkeypatch):
    def install(per_mount):
        fake = FakeVault(per_mount)
        monkeypatch.setattr(config, "get_vault_client", lambda: fake)
        monkeypatch.setattr(env_settings, "use_local_env_secrets", False)
        return fake

    return install


def test_ampay_style_suffixed_and_plain_keys(vault):
    vault({"backend": AMPAY_STYLE})
    db = config.get_brand_db_config("ampay")
    assert (db.host, db.port, db.database, db.user, db.password) == (
        "db.ampay.internal", 5433, "ampay_db", "ampay_user", "s3cret",
    )
    assert db.async_dsn == "postgresql+asyncpg://ampay_user:s3cret@db.ampay.internal:5433/ampay_db"
    assert config.get_brand_redis_config("ampay").url == "redis://redis.ampay.internal:6380/2"
    celery = config.get_brand_celery_config("ampay")
    assert (celery.broker_url, celery.result_backend) == (
        "redis://broker.ampay:6379/1", "redis://broker.ampay:6379/3",
    )
    assert config.get_brand_admin_public_key("ampay") == "pk_admin"
    assert config.get_brand_timezone("ampay") == "Europe/Moscow"
    assert config.get_brand_bot_callback_url("ampay") == "https://bot/cb"


def test_quiet_forest_plain_keys_and_defaults(vault):
    vault({"backend": QF_STYLE})
    db = config.get_brand_db_config("quiet-forest")
    assert (db.host, db.port, db.database) == ("db.qf.internal", 5432, "qf")
    assert config.get_brand_redis_config("quiet-forest").url == "redis://redis.qf:6379/0"
    assert config.get_brand_celery_config("quiet-forest").result_backend is None
    assert config.get_brand_timezone("quiet-forest") == "UTC"
    assert config.get_brand_admin_public_key("quiet-forest") is None
    assert config.get_brand_bot_callback_url("quiet-forest") is None


def test_one_mount_serves_several_brands_via_suffixes(vault):
    shared = {
        **QF_STYLE, "HOST_RAJAPAY": "db.raja", "POSTGRES_DB_RAJAPAY": "raja", "POSTGRES_USER_RAJAPAY": "r",
        "POSTGRES_PASSWORD_RAJAPAY": "rp", "DB_HOST_QUIET_FOREST": "db.qf.suffixed",
    }
    vault({"backend": shared})
    assert config.get_brand_db_config("rajapay").database == "raja"
    assert config.get_brand_db_config("quiet-forest").host == "db.qf.suffixed"  # suffixed beats plain


def test_per_brand_mount_override(vault, monkeypatch):
    fake = vault({"backend": AMPAY_STYLE, "qf-mount": QF_STYLE})
    monkeypatch.setenv("VAULT_MOUNT_QUIET_FOREST", "qf-mount")
    assert config.get_brand_db_config("quiet-forest").host == "db.qf.internal"
    assert fake.mounts_read == ["qf-mount"]


def test_full_redis_url_wins(vault):
    vault({"backend": {**AMPAY_STYLE, "REDIS_URL_AMPAY": "rediss://cache:6379/9"}})
    assert config.get_brand_redis_config("ampay").url == "rediss://cache:6379/9"


def test_missing_required_value_names_the_expected_keys(vault):
    vault({"backend": {"POSTGRES_DB": "x"}})
    with pytest.raises(VaultConfigError, match="HOST_AMPAY"):
        config.get_brand_db_config("ampay")


def test_jwt_secret_from_vault_is_validated(vault):
    config.get_auth_secrets.cache_clear()
    vault({"backend": {"JWT_SECRET_KEY": "short"}})
    with pytest.raises(RuntimeError, match="at least 32"):
        config.get_auth_secrets()
    config.get_auth_secrets.cache_clear()
    vault({"backend": {"JWT_SECRET_KEY": "k" * 48, "ACCESS_TOKEN_EXPIRE_MINUTES": "30"}})
    secrets = config.get_auth_secrets()
    assert secrets.access_token_expire_minutes == 30 and secrets.jwt_algorithm == "HS256"
    config.get_auth_secrets.cache_clear()


class _StubKV:
    def __init__(self, data):
        self.data = data

    def read_secret_version(self, mount_point, path, raise_on_deleted_version):
        if path not in self.data:
            raise RuntimeError("404")
        return {"data": {"data": self.data[path]}}


def _stub_client(monkeypatch, data):
    client = VaultClient()
    hvac_like = SimpleNamespace(secrets=SimpleNamespace(kv=SimpleNamespace(v2=_StubKV(data))))
    monkeypatch.setattr(client, "_get_client", lambda: hvac_like)
    return client


def test_read_merged_combines_paths_later_wins_and_skips_missing(monkeypatch):
    client = _stub_client(monkeypatch, {
        "api_keys": {"A": "1", "X": "api"}, "settings": {"B": "2", "X": "settings"}, "urls": {"C": "3"},
    })
    assert client.read_merged("backend") == {"A": "1", "X": "settings", "B": "2", "C": "3"}


def test_read_merged_with_nothing_readable_fails(monkeypatch):
    client = _stub_client(monkeypatch, {})
    with pytest.raises(VaultConfigError, match="No readable secrets"):
        client.read_merged("backend")
