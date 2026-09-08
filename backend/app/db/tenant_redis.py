"""Per-brand Redis client registry.

Same lazy-cache-by-brand_id pattern as `app.db.tenant_registry` (Postgres)
— one client per brand, created on first use, pointing at the SAME Redis
instance the brand's Django monolith uses for its method-lookup cache.
"""

from __future__ import annotations

import functools

import redis.asyncio as redis

from app.core.brands import is_known_brand
from app.core.config import get_brand_redis_config
from app.db.tenant_registry import UnknownBrandError


@functools.lru_cache(maxsize=32)
def get_tenant_redis(brand_id: str) -> redis.Redis:
    if not is_known_brand(brand_id):
        raise UnknownBrandError(f"Unknown brand_id: {brand_id}")
    config = get_brand_redis_config(brand_id)
    return redis.from_url(config.url, decode_responses=True)


async def dispose_all_redis_clients() -> None:
    from app.core.brands import KNOWN_BRANDS

    for brand_id in KNOWN_BRANDS:
        try:
            client = get_tenant_redis(brand_id)
        except Exception:  # noqa: BLE001, S112 - client may never have been created
            continue
        await client.aclose()
