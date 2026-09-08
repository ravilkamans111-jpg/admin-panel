"""Per-brand async engine registry.

Implements the "separate databases" multi-tenant strategy chosen for this
service: each brand keeps its own Postgres instance (matching how the three
monoliths are actually deployed today — one DB per brand, no shared schema,
no `brand_id` column anywhere in their tables). This registry just resolves
`brand_id` -> an async SQLAlchemy engine/session for that brand's DB,
creating and caching engines lazily on first use.

Lowest layer alongside `app/db/control_plane.py`: pure connection management.
All sessions handed out here are used read-only — see `app.api.deps.get_tenant_session`,
which wraps every session in `SET TRANSACTION READ ONLY`; the repository
layer (`app.repositories`) only ever issues SELECTs on top of it.
"""

from __future__ import annotations

import functools

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.brands import is_known_brand
from app.core.config import get_brand_db_config


class UnknownBrandError(ValueError):
    pass


@functools.lru_cache(maxsize=32)
def _get_engine(brand_id: str) -> AsyncEngine:
    if not is_known_brand(brand_id):
        raise UnknownBrandError(f"Unknown brand_id: {brand_id}")
    db_config = get_brand_db_config(brand_id)
    return create_async_engine(
        db_config.async_dsn,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        # Read-only workload: statements are wrapped in DB-level READ ONLY
        # transactions by app.api.deps.get_tenant_session, not enforced here.
    )


@functools.lru_cache(maxsize=32)
def _get_sessionmaker(brand_id: str) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(_get_engine(brand_id), expire_on_commit=False)


def get_tenant_sessionmaker(brand_id: str) -> async_sessionmaker[AsyncSession]:
    return _get_sessionmaker(brand_id)


async def dispose_all_engines() -> None:
    """Call on shutdown to close pooled connections cleanly."""
    from app.core.brands import KNOWN_BRANDS

    for brand_id in KNOWN_BRANDS:
        try:
            engine = _get_engine(brand_id)
        except Exception:  # noqa: BLE001, S112 - engine may never have been created; nothing to dispose
            continue
        await engine.dispose()
