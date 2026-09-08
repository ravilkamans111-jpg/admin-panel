"""Port of `PaymentMethodCascadeAdmin` / `PaymentMethodCascadeItemInline`
(`api_mediator/admin.py`) — described in the project's own migration notes
as "the most complex validation state machine in the whole codebase". It
lives in the admin layer in source (not the model), so this port lives in
the write service rather than `app.models.tenant`, matching where the
actual checks run.

State machine, ported piece by piece:

1. **Cascade create** (`PaymentMethodCascadeAdmin.save_model`, verbatim):
   ```python
   existing = PaymentMethodCascade.objects.filter(
       name=obj.name, payment_method=obj.payment_method
   ).exclude(pk=obj.pk)
   if existing.exists():
       raise ValidationError(f'Каскад с именем "{obj.name}" для платежного метода "{obj.payment_method}" уже существует')
   ```
   Also `PaymentMethodCascade.clean()` has the identical check — both fire
   in the real Django save path; porting it once here covers both.

2. **`payment_method` is immutable after creation.** Source makes the field
   `readonly_fields` in `get_readonly_fields` once `obj is not None` — not a
   model-level constraint, an admin-only UI lock. Ported the same way: it's
   simply not in `editable_fields` for an update (see the registry entry),
   so a PATCH that includes it 400s before ever reaching this service.

3. **Cascade item create/update** (`PaymentMethodCascadeItem.clean()` +
   `PaymentMethodCascadeItemInline.get_formset().ValidatedFormSet.clean()`,
   both verbatim below — the model-level `clean()` only fires via Django's
   full_clean(), which the admin's formset validation duplicates so both
   single-item saves AND bulk inline saves are covered; this port collapses
   both into one function since there's only one write path here):
   ```python
   # PaymentMethodCascadeItem.clean():
   if self.payment_method_company.payment_method != self.cascade.payment_method:
       raise ValidationError('Платежный метод компании должен соответствовать платежному методу каскада')
   if PaymentMethodCascadeItem.objects.filter(cascade=self.cascade, priority=self.priority).exclude(pk=self.pk).exists():
       raise ValidationError(f'Элемент с приоритетом {self.priority} уже существует в каскаде {self.cascade}')
   ```

4. **`MerchantPaymentMethod.clean()` — the cascade must match the merchant's
   own payment method too**, not just the cascade item's:
   ```python
   if self.cascade and self.cascade.payment_method != self.payment_method:
       raise ValidationError('Каскад должен соответствовать платежному методу мерчанта')
   ```
   This was a genuine gap in the first `MerchantPaymentMethod` write-path
   port (`cascade_id` was made editable there without this check) — closed
   here as part of the same cascade-validation work, in
   `validate_merchant_payment_method_cascade`, called from
   `app.services.payment_method_write_service.update_merchant_payment_method`.

NOT ported (out of scope, no observable write-path consequence found):
  - `PaymentMethodCascadeItemInline.formfield_for_foreignkey`'s dropdown
    filtering — pure UI convenience (narrows a `<select>`), not a
    server-side constraint; the frontend does its own equivalent filtering
    if/when it lists available `PaymentMethodCompany` options.
  - `response_add`'s post-create redirect message — UI-only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RecordNotFoundError
from app.models.tenant import PaymentMethodCascade, PaymentMethodCascadeItem, PaymentMethodCompany
from app.registry.admin_models import get_config
from app.repositories.admin_repository import row_to_dict

_cascade_config = get_config("payment-method-cascades")
assert _cascade_config is not None, "payment-method-cascades must be registered in app.registry.admin_models"
CASCADE_EDITABLE_FIELDS = tuple(_cascade_config.editable_fields)
CASCADE_CREATABLE_FIELDS = tuple(_cascade_config.creatable_fields)

_item_config = get_config("payment-method-cascade-items")
assert _item_config is not None, "payment-method-cascade-items must be registered in app.registry.admin_models"
ITEM_EDITABLE_FIELDS = tuple(_item_config.editable_fields)
ITEM_CREATABLE_FIELDS = tuple(_item_config.creatable_fields)


async def _cascade_name_taken(
    session: AsyncSession, *, name: str, payment_method_id: int, exclude_pk: int | None
) -> bool:
    stmt = select(PaymentMethodCascade.id).where(
        PaymentMethodCascade.name == name, PaymentMethodCascade.payment_method_id == payment_method_id
    )
    if exclude_pk is not None:
        stmt = stmt.where(PaymentMethodCascade.id != exclude_pk)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def create_cascade(session: AsyncSession, *, values: dict[str, Any]) -> dict[str, Any]:
    unknown = [f for f in values if f not in CASCADE_CREATABLE_FIELDS]
    if unknown:
        raise ValueError(f"Not creatable on PaymentMethodCascade: {unknown}")
    for required in ("name", "payment_method_id"):
        if not values.get(required):
            raise ValueError(f"'{required}' is required to create a cascade")

    if await _cascade_name_taken(
        session, name=values["name"], payment_method_id=values["payment_method_id"], exclude_pk=None
    ):
        raise ValueError(
            f"Каскад с именем \"{values['name']}\" для этого платёжного метода уже существует"
        )

    now = datetime.now(UTC)
    cascade = PaymentMethodCascade(
        name=values["name"],
        payment_method_id=values["payment_method_id"],
        description=values.get("description"),
        is_active=values.get("is_active", True),
        date_create=now,
        date_update=now,
    )
    session.add(cascade)
    await session.flush()
    return row_to_dict(cascade)


async def update_cascade(
    session: AsyncSession, *, pk: int, values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = await session.execute(select(PaymentMethodCascade).where(PaymentMethodCascade.id == pk).with_for_update())
    cascade = result.scalar_one_or_none()
    if cascade is None:
        raise RecordNotFoundError(f"PaymentMethodCascade {pk} not found")

    before = row_to_dict(cascade)

    unknown = [f for f in values if f not in CASCADE_EDITABLE_FIELDS]
    if unknown:
        raise ValueError(f"Not editable on PaymentMethodCascade: {unknown}")

    new_name = values.get("name", cascade.name)
    if new_name != cascade.name and await _cascade_name_taken(
        session, name=new_name, payment_method_id=cascade.payment_method_id, exclude_pk=cascade.id
    ):
        raise ValueError(f'Каскад с именем "{new_name}" для платёжного метода "{cascade.payment_method_id}" уже существует')

    for field_name, value in values.items():
        setattr(cascade, field_name, value)
    cascade.date_update = datetime.now(UTC)

    await session.flush()
    after = row_to_dict(cascade)
    return before, after


async def _validate_item_against_cascade(
    session: AsyncSession, *, cascade: PaymentMethodCascade, payment_method_company_id: int, priority: int, exclude_pk: int | None
) -> None:
    pmc = await session.get(PaymentMethodCompany, payment_method_company_id)
    if pmc is None:
        raise ValueError(f"PaymentMethodCompany {payment_method_company_id} not found")
    if pmc.payment_method_id != cascade.payment_method_id:
        raise ValueError(
            "Платёжный метод компании должен соответствовать платёжному методу каскада"
        )

    stmt = select(PaymentMethodCascadeItem.id).where(
        PaymentMethodCascadeItem.cascade_id == cascade.id, PaymentMethodCascadeItem.priority == priority
    )
    if exclude_pk is not None:
        stmt = stmt.where(PaymentMethodCascadeItem.id != exclude_pk)
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise ValueError(f"Элемент с приоритетом {priority} уже существует в этом каскаде")


async def create_cascade_item(
    session: AsyncSession, *, cascade_id: int, values: dict[str, Any]
) -> dict[str, Any]:
    unknown = [f for f in values if f not in ITEM_CREATABLE_FIELDS and f != "cascade_id"]
    if unknown:
        raise ValueError(f"Not creatable on PaymentMethodCascadeItem: {unknown}")
    for required in ("payment_method_company_id", "priority"):
        if values.get(required) is None:
            raise ValueError(f"'{required}' is required to create a cascade item")

    cascade = await session.get(PaymentMethodCascade, cascade_id)
    if cascade is None:
        raise RecordNotFoundError(f"PaymentMethodCascade {cascade_id} not found")

    await _validate_item_against_cascade(
        session,
        cascade=cascade,
        payment_method_company_id=values["payment_method_company_id"],
        priority=values["priority"],
        exclude_pk=None,
    )

    item = PaymentMethodCascadeItem(
        cascade_id=cascade_id,
        payment_method_company_id=values["payment_method_company_id"],
        priority=values["priority"],
        is_active=values.get("is_active", True),
    )
    session.add(item)
    await session.flush()
    return row_to_dict(item)


async def update_cascade_item(
    session: AsyncSession, *, pk: int, values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = await session.execute(
        select(PaymentMethodCascadeItem).where(PaymentMethodCascadeItem.id == pk).with_for_update()
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise RecordNotFoundError(f"PaymentMethodCascadeItem {pk} not found")

    before = row_to_dict(item)

    unknown = [f for f in values if f not in ITEM_EDITABLE_FIELDS]
    if unknown:
        raise ValueError(f"Not editable on PaymentMethodCascadeItem: {unknown}")

    cascade = await session.get(PaymentMethodCascade, item.cascade_id)
    if cascade is None:
        raise RecordNotFoundError(f"PaymentMethodCascade {item.cascade_id} not found")

    new_pmc_id = values.get("payment_method_company_id", item.payment_method_company_id)
    new_priority = values.get("priority", item.priority)
    await _validate_item_against_cascade(
        session,
        cascade=cascade,
        payment_method_company_id=new_pmc_id,
        priority=new_priority,
        exclude_pk=item.id,
    )

    for field_name, value in values.items():
        setattr(item, field_name, value)

    await session.flush()
    after = row_to_dict(item)
    return before, after


async def delete_cascade_item(session: AsyncSession, *, pk: int) -> dict[str, Any]:
    result = await session.execute(
        select(PaymentMethodCascadeItem).where(PaymentMethodCascadeItem.id == pk).with_for_update()
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise RecordNotFoundError(f"PaymentMethodCascadeItem {pk} not found")
    before = row_to_dict(item)
    await session.delete(item)
    await session.flush()
    return before


async def validate_merchant_payment_method_cascade(
    session: AsyncSession, *, payment_method_id: int, cascade_id: int | None
) -> None:
    """Port of `MerchantPaymentMethod.clean()`'s cascade/payment_method
    cross-check — called from `payment_method_write_service` before an edit
    that touches `cascade_id` (or `payment_method_id`, though that field
    isn't editable via this admin either, matching source's own lockdown)."""
    if cascade_id is None:
        return
    cascade = await session.get(PaymentMethodCascade, cascade_id)
    if cascade is None:
        raise ValueError(f"PaymentMethodCascade {cascade_id} not found")
    if cascade.payment_method_id != payment_method_id:
        raise ValueError("Каскад должен соответствовать платёжному методу мерчанта")
