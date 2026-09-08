"""Shared helper for recording write actions to the control-plane audit log.

Every write service (`transaction_write_service`, `settlement_write_service`,
...) calls this after a successful mutation — it's the "who changed what,
from what, to what" trail a payments admin needs before it's trusted with
real write access. Kept separate from `auth_service`'s own audit calls
(login/select-brand) since those don't have a before/after row diff.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import control_plane_repository as repo


async def write_record_change_audit(
    control_plane_session: AsyncSession,
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
    await repo.write_audit_log(
        control_plane_session,
        admin_user_id=admin_user_id,
        brand_id=brand_id,
        action=action,
        detail={
            "model": model_key,
            "record_id": record_id,
            "before": before,
            "after": after,
            **(extra or {}),
        },
        ip_address=ip_address,
    )
