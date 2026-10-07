"""Бизнес-логика generic read-only админ-движка.

Транслирует `ValueError` репозитория (неизвестное поле фильтра) и
отсутствие ключа модели в доменные исключения — обработчик
(`app.api.admin`) о SQLAlchemy/реестре ничего не знает.
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidFilterError, RecordNotFoundError
from app.registry.admin_models import AdminModelConfig, all_configs, config_to_dict, get_config, mask_row
from app.repositories.admin_repository import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
    build_filter_descriptors,
    get_by_pk,
    list_options,
    run_list_query,
)

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "export_csv",
    "get_filter_descriptors",
    "get_options",
    "get_record",
    "get_schema",
    "list_records",
    "mask_page",
    "mask_record",
]

EXPORT_MAX_ROWS = 50_000


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


def _config_or_404(model_key: str) -> AdminModelConfig:
    config = get_config(model_key)
    if config is None:
        raise RecordNotFoundError(f"Unknown admin model '{model_key}'")
    return config


def mask_record(model_key: str, record: dict[str, Any], role: str | None) -> dict[str, Any]:
    return mask_row(_config_or_404(model_key), record, role)


def mask_page(model_key: str, page: Page, role: str | None) -> Page:
    config = _config_or_404(model_key)
    if not config.sensitive_fields:
        return page
    return Page(
        items=[mask_row(config, row, role) for row in page.items],
        total=page.total, page=page.page, page_size=page.page_size,
    )


async def get_filter_descriptors(session: AsyncSession, *, model_key: str) -> list[dict[str, Any]]:
    return await build_filter_descriptors(session, _config_or_404(model_key))


async def get_options(
    session: AsyncSession,
    *,
    model_key: str,
    search: str | None,
    ids: list[int] | None,
    limit: int,
    filters: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    try:
        return await list_options(
            session, _config_or_404(model_key), search=search, ids=ids, limit=limit, filters=filters
        )
    except ValueError as exc:
        raise InvalidFilterError(str(exc)) from exc


def _export_cell(row: dict[str, Any], field: str) -> str:
    """The human value an operator would copy from the screen: the enriched
    name for FK columns (currency code, merchant/partner label), not the id."""
    if field == "currency_id" and row.get("currency_code") is not None:
        return str(row["currency_code"])
    label = row.get(f"{field}_label")
    if label is not None:
        return str(label)
    value = row.get(field)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    return str(value)


async def export_csv(
    session: AsyncSession,
    *,
    model_key: str,
    search: str | None,
    filters: dict[str, str],
    ordering: str | None,
    role: str | None,
) -> AsyncIterator[str]:
    """Streams the whole filtered list (up to EXPORT_MAX_ROWS) as `;`-separated
    CSV with a BOM, which is what Russian-locale Excel opens correctly."""
    config = _config_or_404(model_key)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")

    def flush() -> str:
        data = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return data

    async def fetch(page_number: int) -> Page:
        try:
            return await run_list_query(
                session, config, page=page_number, page_size=MAX_PAGE_SIZE,
                search=search, filters=filters, ordering=ordering,
            )
        except ValueError as exc:
            raise InvalidFilterError(str(exc)) from exc

    # Query before the first yield so a bad filter raises before any bytes are sent.
    page = await fetch(1)
    writer.writerow(config.list_display)
    yield "\ufeff" + flush()

    exported = 0
    while True:
        for row in page.items:
            writer.writerow([_export_cell(mask_row(config, row, role), f) for f in config.list_display])
        exported += len(page.items)
        yield flush()
        if not page.items or exported >= page.total or exported >= EXPORT_MAX_ROWS:
            break
        page = await fetch(page.page + 1)


async def get_record(session: AsyncSession, *, model_key: str, pk: int) -> dict[str, Any]:
    config = get_config(model_key)
    if config is None:
        raise RecordNotFoundError(f"Unknown admin model '{model_key}'")
    record = await get_by_pk(session, config, pk)
    if record is None:
        raise RecordNotFoundError(f"{config.verbose_name} {pk} не найден(а)")
    return record
