"""HTTP handlers for the generic read-only admin surface.

Thin: extracts query params, delegates to `app.services.admin_service`, maps
its domain exceptions to HTTP status codes. No query-building or registry
lookups happen here directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_tenant_session
from app.core.exceptions import InvalidFilterError, RecordNotFoundError
from app.services import admin_service
from app.services.admin_service import DEFAULT_PAGE_SIZE

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/schema")
async def get_schema(current_user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """Drives the frontend nav + generic table columns/filters — one call, no
    per-model frontend code needed to add support for a new model."""
    return admin_service.get_schema()


_RESERVED_QUERY_PARAMS = {"page", "page_size", "search", "ordering"}


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
    # Any query param besides the reserved ones is treated as `list_filter`
    # field=value equality filter — e.g. ?status=SUCCESS&merchant_id=42.
    filters = {k: v for k, v in request.query_params.items() if k not in _RESERVED_QUERY_PARAMS}
    try:
        result = await admin_service.list_records(
            session, model_key=model_key, page=page, page_size=page_size, search=search, filters=filters, ordering=ordering
        )
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvalidFilterError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

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
        return await admin_service.get_record(session, model_key=model_key, pk=pk)
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
