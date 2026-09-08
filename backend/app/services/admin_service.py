"""Бизнес-логика generic read-only админ-движка.

Транслирует `ValueError` репозитория (неизвестное поле фильтра) и
отсутствие ключа модели в доменные исключения — обработчик
(`app.api.admin`) о SQLAlchemy/реестре ничего не знает.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidFilterError, RecordNotFoundError
from app.registry.admin_models import all_configs, config_to_dict, get_config
from app.repositories.admin_repository import DEFAULT_PAGE_SIZE, Page, get_by_pk, run_list_query

__all__ = ["DEFAULT_PAGE_SIZE", "get_record", "get_schema", "list_records"]


def get_schema() -> list[dict[str, Any]]:
    return [config_to_dict(c) for c in all_configs()]


async def list_records(
    session: AsyncSession,
    *,
    model_key: str,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search: str | None = None,
    filters: dict[str, str] | None = None,
    ordering: str | None = None,
) -> Page:
    config = get_config(model_key)
    if config is None:
        raise RecordNotFoundError(f"Unknown admin model '{model_key}'")
    try:
        return await run_list_query(
            session, config, page=page, page_size=page_size, search=search, filters=filters, ordering=ordering
        )
    except ValueError as exc:
        raise InvalidFilterError(str(exc)) from exc


async def get_record(session: AsyncSession, *, model_key: str, pk: int) -> dict[str, Any]:
    config = get_config(model_key)
    if config is None:
        raise RecordNotFoundError(f"Unknown admin model '{model_key}'")
    record = await get_by_pk(session, config, pk)
    if record is None:
        raise RecordNotFoundError(f"{config.verbose_name} {pk} не найден(а)")
    return record
