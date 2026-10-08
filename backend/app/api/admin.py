"""HTTP handlers for the generic read-only admin surface.

Thin: extracts query params, delegates to `app.services.admin_service`, maps
its domain exceptions to HTTP status codes. No query-building or registry
lookups happen here directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_tenant_session
from app.core.exceptions import InvalidFilterError, RecordNotFoundError
from app.core.ttl_cache import TTLCache
from app.services import admin_service
from app.services.admin_service import DEFAULT_PAGE_SIZE

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/schema")
async def get_schema(current_user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """Drives the frontend nav + generic table columns/filters — one call, no
    per-model frontend code needed to add support for a new model."""
    return admin_service.get_schema()


_RESERVED_QUERY_PARAMS = {"page", "page_size", "search", "ordering"}

# `SELECT DISTINCT` over big tables for the choice lists runs on every list page open;
# the set of statuses/directions barely changes, so keep it for five minutes.
_filter_options_cache = TTLCache(ttl_seconds=300)


def _filters_from(request: Request) -> dict[str, str]:
    # Any query param besides the reserved ones is a `list_filter` /
    # `date_filters` / `virtual_filters` entry — ?status=SUCCESS,ACCEPTED,
    # ?date_create__gte=2026-08-01 (comma-separated = any of).
    return {k: v for k, v in request.query_params.items() if k not in _RESERVED_QUERY_PARAMS}


@router.get("/{model_key}/filter-options")
async def filter_options(
    model_key: str,
    session: AsyncSession = Depends(get_tenant_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """Descriptors the list page renders its filter controls from."""
    try:
        return await _filter_options_cache.get_or_compute(
            (current_user.brand_id, model_key),
            lambda: admin_service.get_filter_descriptors(session, model_key=model_key),
        )
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/{model_key}/options")
async def options(
    model_key: str,
    request: Request,
    search: str | None = Query(None),
    ids: str | None = Query(None, description="comma-separated ids to resolve to labels"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_tenant_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """`[{id, label}]` for searchable pickers and FK filters."""
    try:
        id_list = [int(i) for i in ids.split(",") if i.strip()] if ids else None
        # Anything beyond search/ids/limit narrows the choices (e.g. `?payment_method_id=12`
        # on partner methods, like the monolith's cascade form does).
        narrowing = {k: v for k, v in request.query_params.items() if k not in {"search", "ids", "limit"}}
        return await admin_service.get_options(
            session, model_key=model_key, search=search, ids=id_list, limit=limit, filters=narrowing
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvalidFilterError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/{model_key}/export")
async def export_records(
    model_key: str,
    request: Request,
    search: str | None = Query(None),
    ordering: str | None = Query(None),
    session: AsyncSession = Depends(get_tenant_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """The whole filtered list as CSV (same filters/search/ordering as the
    list endpoint), capped at `admin_service.EXPORT_MAX_ROWS` rows."""
    if admin_service.get_config(model_key) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown admin model '{model_key}'")
    stream = admin_service.export_csv(
        session, model_key=model_key, search=search, filters=_filters_from(request),
        ordering=ordering, role=current_user.role,
    )
    try:
        first_chunk = await anext(stream)  # surface bad filters as a 400 before streaming starts
    except InvalidFilterError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    async def body():
        yield first_chunk
        async for chunk in stream:
            yield chunk

    return StreamingResponse(
        body(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{model_key}.csv"'},
    )


@router.get("/{model_key}")
async def list_records(
    model_key: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=200),
    search: str | None = Query(None),
    ordering: str | None = Query(None),
    session: AsyncSession = Depends(get_tenant_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    filters = _filters_from(request)
    try:
        result = await admin_service.list_records(
            session, model_key=model_key, page=page, page_size=page_size, search=search, filters=filters, ordering=ordering
        )
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvalidFilterError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    result = admin_service.mask_page(model_key, result, current_user.role)
    return {
        "items": result.items,
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
    }


@router.get("/{model_key}/{pk}")
async def retrieve_record(
    model_key: str,
    pk: int,
    session: AsyncSession = Depends(get_tenant_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        record = await admin_service.get_record(session, model_key=model_key, pk=pk)
        return admin_service.mask_record(model_key, record, current_user.role)
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
