"""Per-brand Celery task producer registry.

This service never runs a Celery *worker* — only a producer that enqueues
tasks by name onto the SAME broker the brand's Django monolith's workers
consume from, so those workers (not this service) actually execute them
(e.g. `CallbacksService.send_message_to_merchant`). `celery.Celery.send_task`
is a blocking call (kombu has no first-class asyncio API), so it's always
run via `asyncio.to_thread` from async call sites — see
`app.services.transaction_write_service` for the caller.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any

from celery import Celery

from app.core.brands import is_known_brand
from app.core.config import get_brand_celery_config
from app.db.tenant_registry import UnknownBrandError


@functools.lru_cache(maxsize=32)
def get_tenant_celery_app(brand_id: str) -> Celery:
    if not is_known_brand(brand_id):
        raise UnknownBrandError(f"Unknown brand_id: {brand_id}")
    config = get_brand_celery_config(brand_id)
    app = Celery(f"brand-admin-panel-producer-{brand_id}", broker=config.broker_url)
    if config.result_backend:
        app.conf.result_backend = config.result_backend
    return app


async def send_task(brand_id: str, task_name: str, *, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> str:
    """Enqueues `task_name` onto the brand's real Celery broker, from async code.

    Returns the enqueued task's id (for logging/audit — this service never
    awaits the task's result, matching the `.delay()` fire-and-forget calls
    in the source `TransactionAdmin`/`SettlementsAdmin` actions).
    """
    app = get_tenant_celery_app(brand_id)

    def _dispatch() -> str:
        result = app.send_task(task_name, args=args or [], kwargs=kwargs or {})
        return result.id

    return await asyncio.to_thread(_dispatch)
