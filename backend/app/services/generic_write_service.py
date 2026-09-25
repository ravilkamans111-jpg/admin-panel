"""Generic create/update/delete for "plain" admin models — ones whose source
`ModelAdmin` has no `save_model`/`clean`/custom action overriding Django's
default form-save behavior (confirmed by grep across every `admin.py` in
all three monoliths; the only overrides found were `PaymentMethodCascade`'s
`clean`/`save_model`, already ported in `app.services.cascade_write_service`,
and the money-mutating Group B models, each with its own dedicated write
service). For everything else, Django's default `ModelAdmin` behavior IS
"set the submitted fields on the instance and save" — which is exactly what
this module does, config-driven off `AdminModelConfig.creatable_fields`/
`editable_fields`/`creatable`/`deletable` (see `app.registry.admin_models`).

Deliberately NOT used for any model with its own dedicated `*_write_service`
(Transaction, Settlements, AntiFraudBlockedMerchantUsers,
PaymentMethodCompany, MerchantPaymentMethod, PaymentMethodCascade[Item],
MerchantBalance) — those need real business logic on save, not a plain
column copy, and their registry entries' `editable_fields`/`creatable_fields`
are consumed by their own services instead. Routing here happens purely
because `app.api.generic_writes`'s catch-all routes are registered AFTER
every dedicated write router in `app.main` — FastAPI matches the first
route whose path matches, so `/admin/transactions/{pk}` PATCH always hits
`app.api.transaction_writes` first and this module is never reached for it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RecordNotFoundError
from app.registry.admin_models import AdminModelConfig
from app.repositories.admin_repository import (
    apply_casted_field_updates,
    build_instance,
    get_instance_by_pk,
    row_to_dict,
)

# Columns this generic engine auto-manages when the model has them AND they
# are NOT in `creatable_fields`/`editable_fields` — mirrors Django's
# `default=timezone.now` (date_create) / `auto_now=True` (date_update),
# which make a field un-settable through the ModelForm but not through the
# model itself. Only `SellingInfo` needs this among the currently-generic
# models (confirmed by checking every other candidate's real column
# defaults) but this is generic on purpose so any future plain model with
# the same column names gets the same behavior for free.
_AUTO_CREATE_TIMESTAMP = "date_create"
_AUTO_TOUCH_TIMESTAMP = "date_update"


def _touch_timestamps(config: AdminModelConfig, instance: Any, *, is_create: bool) -> None:
    now = datetime.now(UTC)
    columns = {c.key for c in instance.__mapper__.columns}
    if (
        is_create
        and _AUTO_CREATE_TIMESTAMP in columns
        and _AUTO_CREATE_TIMESTAMP not in config.creatable_fields
        and getattr(instance, _AUTO_CREATE_TIMESTAMP, None) is None
    ):
        setattr(instance, _AUTO_CREATE_TIMESTAMP, now)
    if _AUTO_TOUCH_TIMESTAMP in columns and _AUTO_TOUCH_TIMESTAMP not in config.editable_fields:
        setattr(instance, _AUTO_TOUCH_TIMESTAMP, now)


async def create_record(session: AsyncSession, *, config: AdminModelConfig, values: dict[str, Any]) -> dict[str, Any]:
    if not config.creatable:
        raise ValueError(f"{config.key} is not creatable")
    instance = build_instance(config, values)
    _touch_timestamps(config, instance, is_create=True)
    session.add(instance)
    await session.flush()
    return row_to_dict(instance)


async def update_record(
    session: AsyncSession, *, config: AdminModelConfig, pk: int, values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not config.editable_fields:
        raise ValueError(f"{config.key} is not editable")
    instance = await get_instance_by_pk(session, config, pk)
    if instance is None:
        raise RecordNotFoundError(f"{config.verbose_name} {pk} не найден(а)")
    before = row_to_dict(instance)
    apply_casted_field_updates(config, instance, values)
    _touch_timestamps(config, instance, is_create=False)
    await session.flush()
    after = row_to_dict(instance)
    return before, after


async def delete_record(session: AsyncSession, *, config: AdminModelConfig, pk: int) -> dict[str, Any]:
    if not config.deletable:
        raise ValueError(f"{config.key} is not deletable")
    instance = await get_instance_by_pk(session, config, pk)
    if instance is None:
        raise RecordNotFoundError(f"{config.verbose_name} {pk} не найден(а)")
    before = row_to_dict(instance)
    await session.delete(instance)
    await session.flush()
    return before
