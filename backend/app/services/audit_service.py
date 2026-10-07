"""Audit trail of write actions — recorded where the monolith records its own.

Every successful mutation is (1) emitted as a structured line on the `audit`
logger and (2) inserted into the brand database's Django `django_admin_log`
(the table behind the admin's "History" and "Recent actions"), attributed to
the staff member's real `auth_user.id`. So changes made here show up in the
monolith's admin next to changes made there, and no database of ours is needed.

Best-effort by design: the business change has already been committed when
this runs, so a failed audit insert is logged loudly but never turns a
successful edit into an error response.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

from app.db.tenant_registry import get_tenant_sessionmaker
from app.registry.admin_models import get_config, mask_row

logger = logging.getLogger("audit")

ACTION_ADDITION, ACTION_CHANGE, ACTION_DELETION = 1, 2, 3  # django.contrib.admin.models

_content_type_ids: dict[tuple[str, str, str], int | None] = {}


def _action_flag(action: str) -> int:
    if action.startswith(("create", "add")):
        return ACTION_ADDITION
    if action.startswith("delete"):
        return ACTION_DELETION
    return ACTION_CHANGE


def _change_message(flag: int, action: str, before: dict | None, after: dict | None, extra: dict | None) -> str:
    """Django's own JSON shape for add/change (what its History page renders);
    plain text for everything else (bulk actions, cache clears, ...)."""
    if flag == ACTION_ADDITION:
        return json.dumps([{"added": {}}], ensure_ascii=False)
    if flag == ACTION_CHANGE and before and after:
        fields = [k for k in after if k in before and before[k] != after[k]]
        if fields:
            return json.dumps([{"changed": {"fields": fields}}], ensure_ascii=False)
    if flag == ACTION_DELETION:
        return json.dumps([{"deleted": {}}], ensure_ascii=False)
    return f"{action}: {json.dumps(extra, ensure_ascii=False, default=str)}" if extra else action


async def _content_type_id(session, brand_id: str, app_label: str, model_name: str) -> int | None:
    key = (brand_id, app_label, model_name)
    if key not in _content_type_ids:
        row = (
            await session.execute(
                text("SELECT id FROM django_content_type WHERE app_label = :a AND model = :m"),
                {"a": app_label, "m": model_name},
            )
        ).first()
        _content_type_ids[key] = row[0] if row else None
    return _content_type_ids[key]


async def write_record_change_audit(
    *,
    admin_user_id: int,
    brand_id: str,
    action: str,
    model_key: str,
    record_id: int | str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    extra: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    config = get_config(model_key)
    if config is not None:
        # Audit trails must never hold secrets, whatever the actor's role.
        before = mask_row(config, before, None) if before else before
        after = mask_row(config, after, None) if after else after

    logger.info(
        json.dumps(
            {"event": action, "brand": brand_id, "user_id": admin_user_id, "model": model_key,
             "record_id": record_id, "ip": ip_address, "before": before, "after": after, **(extra or {})},
            ensure_ascii=False, default=str,
        )
    )

    flag = _action_flag(action)
    label = config.verbose_name if config else model_key
    try:
        async with get_tenant_sessionmaker(brand_id)() as session:
            content_type_id = (
                await _content_type_id(session, brand_id, config.app, config.model.__name__.lower()) if config else None
            )
            await session.execute(
                text(
                    "INSERT INTO django_admin_log "
                    "(action_time, object_id, object_repr, action_flag, change_message, content_type_id, user_id) "
                    "VALUES (CURRENT_TIMESTAMP, :object_id, :object_repr, :flag, :message, :ct, :user_id)"
                ),
                {
                    "object_id": str(record_id), "object_repr": f"{label} #{record_id}"[:200], "flag": flag,
                    "message": _change_message(flag, action, before, after, extra), "ct": content_type_id,
                    "user_id": admin_user_id,
                },
            )
            await session.commit()
    except Exception:
        logger.exception("audit: could not write django_admin_log (brand=%s action=%s record=%s)", brand_id, action, record_id)
