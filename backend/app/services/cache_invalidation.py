"""Port of `personal_account_transaction.business_logic.services.window.cache_invalidation`
(Redis-backed Django cache invalidation for the merchant payment-method
lookup cache — the thing `PaymentMethodCompany.save()` and
`MerchantPaymentMethod.save()` bust on every write).

Source (verbatim, from `cache_invalidation.py`):

```python
def invalidate_cache(currency: str | None = None, merchant_id: int | None = None) -> None:
    if currency and not merchant_id:
        delete_methods_from_cache(f'merchant_methods:*:{currency}')
        delete_methods_from_cache('merchant_methods:*:all')
    elif merchant_id and not currency:
        delete_methods_from_cache(f'merchant_methods:{merchant_id}:*')
    elif currency and merchant_id:
        delete_methods_from_cache(f'merchant_methods:{merchant_id}:{currency}')
        delete_methods_from_cache(f'merchant_methods:{merchant_id}:all')
    else:
        delete_methods_from_cache('merchant_methods:*')  # full reset
```

Source reads/writes this cache through Django's `cache` object, backed by
`django_redis.cache.RedisCache` with no explicit `KEY_PREFIX`/`VERSION` in
`CACHES` (see `core/settings.py`) — Django's default key function is
`f"{key_prefix}:{version}:{key}"`, which with an empty prefix and version=1
means every real Redis key is literally `:1:<key>`, e.g.
`:1:merchant_methods:5:USD`. `_django_key` below reproduces that exact
shape so this port busts the SAME keys the Django monolith itself reads.

Source deletes via the blocking `KEYS` command (`cache.keys(pattern)` then
`delete_many`). This port uses `SCAN` instead — same matched-and-deleted
key set, non-blocking — a deliberate operational improvement, not a
behavior change (same precedent as the row-locking added in
`balance_math.py`).

Source's own `delete_methods_from_cache` never lets a cache error surface
(`except Exception: logger.error(...)`) — a Redis outage does not fail the
underlying model save. Callers here must preserve that: invalidation
failures are swallowed and logged, never re-raised into the write path.
"""

from __future__ import annotations

import logging
from typing import Any

import redis.asyncio as redis

from app.db.tenant_redis import get_tenant_redis

logger = logging.getLogger(__name__)

_KEY_VERSION = 1


def _django_key(pattern: str) -> str:
    """Reproduces Django's default cache key_func with the source's
    effectively-empty `KEY_PREFIX` and `VERSION=1`."""
    return f":{_KEY_VERSION}:{pattern}"


async def _delete_by_pattern(client: redis.Redis, pattern: str) -> None:
    real_pattern = _django_key(pattern)
    deleted = 0
    try:
        async for key in client.scan_iter(match=real_pattern):
            await client.delete(key)
            deleted += 1
        if deleted:
            logger.info("Удалено %d ключей кеша по паттерну: %s", deleted, real_pattern)
    except Exception:
        logger.exception("Ошибка при удалении кеша для паттерна %s", real_pattern)


async def invalidate_cache(
    client: redis.Redis, *, currency: str | None = None, merchant_id: int | None = None
) -> None:
    """Port of `invalidate_cache` — identical 4-way branch, identical key shapes."""
    if currency and not merchant_id:
        await _delete_by_pattern(client, f"merchant_methods:*:{currency}")
        await _delete_by_pattern(client, "merchant_methods:*:all")
        logger.info("Сброшен кеш методов для валюты: %s", currency)
    elif merchant_id and not currency:
        await _delete_by_pattern(client, f"merchant_methods:{merchant_id}:*")
        logger.info("Сброшен кеш методов для мерчанта: %s", merchant_id)
    elif currency and merchant_id:
        await _delete_by_pattern(client, f"merchant_methods:{merchant_id}:{currency}")
        await _delete_by_pattern(client, f"merchant_methods:{merchant_id}:all")
        logger.info("Сброшен кеш для мерчанта %s, валюта: %s", merchant_id, currency)
    else:
        await _delete_by_pattern(client, "merchant_methods:*")
        logger.warning("Выполнен полный сброс кеша методов оплаты")


async def safe_invalidate_cache(brand_id: str, **kwargs: Any) -> None:
    """`invalidate_cache`, but never lets a Redis problem (missing config,
    connection failure) propagate to the caller — the same non-fatal
    guarantee source's own `delete_methods_from_cache` gives, extended here
    to also cover "Redis isn't configured for this brand at all" (a real
    possibility in local/dev environments). Every write path and bulk
    cache-clear admin action should call this rather than `invalidate_cache`
    directly."""
    try:
        client = get_tenant_redis(brand_id)
        await invalidate_cache(client, **kwargs)
    except Exception:
        logger.exception("Не удалось инвалидировать кэш методов оплаты для бренда %s", brand_id)
