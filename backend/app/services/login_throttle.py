"""Brute-force protection for the login endpoint.

The monolith's admin has none; with no database of our own to keep counters in,
they live in Redis (the first brand whose Redis answers) with a TTL, falling
back to per-process memory when Redis is unreachable — never fail-open to
"unlimited tries". Keyed by lower-cased username, so spraying passwords at one
account is stopped regardless of source IP (IP limits belong on the proxy).
"""

from __future__ import annotations

import contextlib
import logging
import time

from app.core.brands import KNOWN_BRANDS
from app.core.settings_env import env_settings
from app.db.tenant_redis import get_tenant_redis

logger = logging.getLogger(__name__)

_memory: dict[str, tuple[int, float]] = {}  # key -> (failures, window_expires_at)


def _key(username: str) -> str:
    return f"bap:login_fail:{username.strip().lower()}"


async def _redis():
    for brand_id in KNOWN_BRANDS:
        with contextlib.suppress(Exception):  # try the next brand's Redis
            client = get_tenant_redis(brand_id)
            await client.ping()
            return client
    return None


async def is_locked(username: str) -> bool:
    key, limit = _key(username), env_settings.login_max_failed_attempts
    client = await _redis()
    if client is not None:
        try:
            return int(await client.get(key) or 0) >= limit
        except Exception:  # noqa: BLE001
            logger.warning("login throttle: Redis read failed, using memory")
    failures, expires = _memory.get(key, (0, 0.0))
    return failures >= limit and expires > time.time()


async def register_failure(username: str) -> None:
    key, window = _key(username), env_settings.login_lockout_minutes * 60
    client = await _redis()
    if client is not None:
        try:
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, window)
            return
        except Exception:  # noqa: BLE001
            logger.warning("login throttle: Redis write failed, using memory")
    failures, expires = _memory.get(key, (0, 0.0))
    if expires <= time.time():
        failures, expires = 0, time.time() + window
    _memory[key] = (failures + 1, expires)


async def clear(username: str) -> None:
    key = _key(username)
    _memory.pop(key, None)
    client = await _redis()
    if client is not None:
        try:
            await client.delete(key)
        except Exception:  # noqa: BLE001
            logger.warning("login throttle: Redis delete failed")
