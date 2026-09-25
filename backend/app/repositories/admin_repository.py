"""Generic read-only query builder over any registered admin model.

Data-access layer for `app.services.admin_service`: takes an
`AdminModelConfig` (from `app.registry.admin_models`) plus filter/search/
pagination parameters and issues the SELECTs. Raises plain `ValueError` for
bad field names — the service layer translates that into a domain exception
(`InvalidFilterError`), keeping this module free of any HTTP awareness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import JSON, String, Text, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models.tenant import Currency
from app.registry.admin_models import AdminModelConfig

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class Page:
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


def _column(model: type, name: str):
    if not hasattr(model, name):
        raise ValueError(f"Unknown field '{name}' on {model.__name__}")
    return getattr(model, name)


def _cast_filter_value(column, raw: str) -> Any:
    py_type = column.type.python_type if hasattr(column.type, "python_type") else str
    if py_type is bool:
        return raw.lower() in ("1", "true", "yes", "on")
    if py_type is int:
        return int(raw)
    if py_type is Decimal:
        try:
            return Decimal(raw)
        except InvalidOperation:
            raise ValueError(f"Invalid decimal filter value: {raw}") from None
    return raw


def cast_value(column, raw: Any) -> Any:
    """Coerces a raw form-submitted value (almost always a `str`, sometimes
    already a native type from a JSON body) to the column's Python type —
    the write-path counterpart to `_cast_filter_value`. `None` / `""` maps
    to `NULL` for nullable columns (source's Django admin does the same:
    an empty optional form field saves as `NULL`, not `""`)."""
    if raw is None or raw == "":
        return None
    if isinstance(column.type, JSON):
        return json.loads(raw) if isinstance(raw, str) else raw
    py_type = column.type.python_type if hasattr(column.type, "python_type") else str
    if not isinstance(raw, str):
        return raw
    if py_type is bool:
        return raw.lower() in ("1", "true", "yes", "on")
    if py_type is int:
        return int(raw)
    if py_type is Decimal:
        try:
            return Decimal(raw)
        except InvalidOperation:
            raise ValueError(f"Invalid decimal value: {raw}") from None
    return raw


def build_instance(config: AdminModelConfig, values: dict[str, Any]) -> Any:
    """Builds a new, unsaved ORM instance from `config.creatable_fields`.

    Mirrors `apply_field_updates`'s field-allowlist discipline: any key not
    in `creatable_fields` is rejected rather than silently dropped."""
    unknown = [f for f in values if f not in config.creatable_fields]
    if unknown:
        raise ValueError(f"Not creatable on {config.key}: {unknown}")
    kwargs: dict[str, Any] = {}
    for field_name, raw in values.items():
        column = _column(config.model, field_name)
        kwargs[field_name] = cast_value(column, raw)
    return config.model(**kwargs)


def apply_casted_field_updates(config: AdminModelConfig, instance: Any, values: dict[str, Any]) -> None:
    """Like `apply_field_updates`, but casts each raw value to the target
    column's Python type first — for the generic write engine, whose
    incoming values are always raw strings from a form, unlike the
    money-mutating services' own dedicated request models (which use
    Pydantic to cast before ever reaching the repository)."""
    unknown = [f for f in values if f not in config.editable_fields]
    if unknown:
        raise ValueError(f"'{unknown[0]}' is not an editable field for {config.key}")
    for field_name, raw in values.items():
        column = _column(config.model, field_name)
        setattr(instance, field_name, cast_value(column, raw))


def _apply_filters(stmt: Select, config: AdminModelConfig, *, search: str | None, filters: dict[str, str]) -> Select:
    model = config.model
    if search and config.search_fields:
        search_clauses = []
        for field_name in config.search_fields:
            column = _column(model, field_name)
            if isinstance(column.type, (String, Text)):
                search_clauses.append(column.ilike(f"%{search}%"))
        if search_clauses:
            stmt = stmt.where(or_(*search_clauses))

    for field_name, raw_value in filters.items():
        if field_name not in config.list_filter:
            raise ValueError(f"'{field_name}' is not a filterable field for {config.key}")
        column = _column(model, field_name)
        stmt = stmt.where(column == _cast_filter_value(column, raw_value))

    return stmt


async def run_list_query(
    session: AsyncSession,
    config: AdminModelConfig,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search: str | None = None,
    filters: dict[str, str] | None = None,
    ordering: str | None = None,
) -> Page:
    filters = filters or {}
    page_size = min(page_size, MAX_PAGE_SIZE)
    model = config.model

    filtered_stmt = _apply_filters(select(model), config, search=search, filters=filters)

    count_stmt = _apply_filters(
        select(func.count()).select_from(model), config, search=search, filters=filters
    )
    total = (await session.execute(count_stmt)).scalar_one()

    order_fields = [ordering] if ordering else config.default_ordering
    list_stmt = filtered_stmt
    for order_field in order_fields:
        descending = order_field.startswith("-")
        field_name = order_field.lstrip("-")
        column = _column(model, field_name)
        list_stmt = list_stmt.order_by(column.desc() if descending else column.asc())
    list_stmt = list_stmt.offset((page - 1) * page_size).limit(page_size)

    rows = (await session.execute(list_stmt)).scalars().all()
    items = [row_to_dict(row) for row in rows]
    await _enrich_currency_codes(session, items)
    return Page(items=items, total=total, page=page, page_size=page_size)


async def _enrich_currency_codes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    """Best-effort: any row with a `currency_id` column (MerchantBalance,
    CompanyBalance, PaymentMethod, Card, PaymentMethodTemplate, ...) gets a
    sibling `currency_code` (the ISO code) added in place, so the frontend
    can show a real currency instead of a bare numeric id — without
    changing `list_display`/column order for any model's registry entry."""
    ids = {row["currency_id"] for row in rows if row.get("currency_id") is not None}
    if not ids:
        return
    result = await session.execute(select(Currency.id, Currency.iso_code).where(Currency.id.in_(ids)))
    code_by_id = dict(result.all())
    for row in rows:
        currency_id = row.get("currency_id")
        if currency_id is not None and currency_id in code_by_id:
            row["currency_code"] = code_by_id[currency_id]


async def get_by_pk(session: AsyncSession, config: AdminModelConfig, pk: int) -> dict[str, Any] | None:
    column = _column(config.model, config.pk_field)
    stmt = select(config.model).where(column == pk)
    instance = (await session.execute(stmt)).scalar_one_or_none()
    if instance is None:
        return None
    row = row_to_dict(instance)
    await _enrich_currency_codes(session, [row])
    return row


async def get_instance_by_pk(session: AsyncSession, config: AdminModelConfig, pk: int) -> Any | None:
    """Like `get_by_pk` but returns the live ORM instance, not a dict — for
    write paths that need to mutate it in place (and, in the money-mutating
    services, hold onto it across several related updates in one flush)."""
    column = _column(config.model, config.pk_field)
    stmt = select(config.model).where(column == pk)
    return (await session.execute(stmt)).scalar_one_or_none()


def apply_field_updates(config: AdminModelConfig, instance: Any, values: dict[str, Any]) -> None:
    """Sets only fields declared in `config.editable_fields` on `instance`.

    Raises `ValueError` for anything else — the write service (not this
    generic repository) already validated the field list came from a source
    admin.py's real editable fields, so a client sending an unlisted field
    is either a bug or an attempted bypass, not something to silently drop.
    """
    for field_name, value in values.items():
        if field_name not in config.editable_fields:
            raise ValueError(f"'{field_name}' is not an editable field for {config.key}")
        setattr(instance, field_name, value)


def row_to_dict(instance: Any) -> dict[str, Any]:
    mapper = instance.__mapper__
    result: dict[str, Any] = {}
    for column in mapper.columns:
        value = getattr(instance, column.key)
        if isinstance(value, Decimal):
            value = str(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        result[column.key] = value
    return result
