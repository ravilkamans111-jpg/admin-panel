"""Port of `MerchantAdmin`'s two bulk actions (`personal_account_auth/admin.py`):
`apply_template_to_merchants` and `apply_selected_methods_to_merchants` —
both provision `MerchantPaymentMethod` rows for many merchants at once from
a single admin action, each with its own intermediate confirmation screen
in source (a Django `render()` of `select_template.html` / `select_methods.html`
before the actual `POST ... apply`).

**Important preserved quirk**: both actions create rows via Django's
`MerchantPaymentMethod.objects.bulk_create(...)`, which — unlike calling
`.save()` on each instance — does **not** invoke `save()` and therefore does
**not** run `invalidate_cache(merchant_id=...)` (see
`app.services.payment_method_write_service.update_merchant_payment_method`,
which DOES invalidate, because it goes through the normal edit path). A
merchant who gets payment methods provisioned via either bulk action may
keep serving a stale (empty/incomplete) cached method list until something
else happens to bust it. This is a genuine bug in source, not a design
choice — preserved here bug-for-bug per the project's "1:1 including bugs"
scope decision: neither function below calls cache invalidation.

Source (verbatim, `apply_template_to_merchants`, POST/apply branch):
```python
template = PaymentMethodTemplate.objects.prefetch_related("method_mappings__payment_method").get(id=template_id)
method_mappings = template.method_mappings.all()
payment_methods = [mapping.payment_method for mapping in method_mappings]
existing_pairs = set(MerchantPaymentMethod.objects.filter(
    merchant__in=merchants, payment_method__in=payment_methods,
).values_list("merchant_id", "payment_method_id"))
objects_to_create = []
for merchant in merchants:
    for mapping in method_mappings:
        if (merchant.id, mapping.payment_method.id) not in existing_pairs:
            objects_to_create.append(MerchantPaymentMethod(
                merchant=merchant, payment_method=mapping.payment_method,
                personal_rate=template.default_personal_rate,
                transaction_min_limit=template.transaction_min_limit,
                transaction_max_limit=template.transaction_max_limit,
                test_mode=template.test_mode, only_admin_configure=template.only_admin_configure,
            ))
with transaction.atomic():
    if objects_to_create:
        MerchantPaymentMethod.objects.bulk_create(objects_to_create)
```
Existing (merchant, payment_method) pairs are silently skipped — no error,
no overwrite of an existing row's rate/limits. Ported identically.

Source (verbatim skeleton, `apply_selected_methods_to_merchants`, POST/apply
branch) — same existing-pair skip-and-bulk_create shape, but the method set
comes from a dynamic currency+direction-filtered checkbox list
(`PaymentMethodSelectionForm`) instead of a template, and the
rate/limits/test_mode/only_admin_configure values are typed in directly
rather than copied from a template row. Ported with the same semantics:
only methods matching BOTH the given `currency_id` and `direction` AND
present in the given `method_ids` are used (mirrors
`PaymentMethod.objects.filter(id__in=selected_methods, currency=currency, direction=direction)`
— the currency/direction filter is a real narrowing, not just an id lookup).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RecordNotFoundError
from app.models.tenant import (
    Merchant,
    MerchantPaymentMethod,
    PaymentMethod,
    PaymentMethodTemplate,
    TemplateMethodMapping,
)


@dataclass(frozen=True, slots=True)
class BulkApplyResult:
    created_count: int
    skipped_existing_count: int


async def _existing_pairs(
    session: AsyncSession, *, merchant_ids: list[int], payment_method_ids: list[int]
) -> set[tuple[int, int]]:
    if not merchant_ids or not payment_method_ids:
        return set()
    result = await session.execute(
        select(MerchantPaymentMethod.merchant_id, MerchantPaymentMethod.payment_method_id).where(
            MerchantPaymentMethod.merchant_id.in_(merchant_ids),
            MerchantPaymentMethod.payment_method_id.in_(payment_method_ids),
        )
    )
    return set(result.all())


async def apply_template_to_merchants(
    session: AsyncSession, *, merchant_ids: list[int], template_id: int
) -> BulkApplyResult:
    if not merchant_ids:
        raise ValueError("Не выбрано ни одного мерчанта")

    template = await session.get(PaymentMethodTemplate, template_id)
    if template is None:
        raise RecordNotFoundError(f"PaymentMethodTemplate {template_id} not found")

    result = await session.execute(
        select(TemplateMethodMapping).where(TemplateMethodMapping.template_id == template_id)
    )
    mappings = list(result.scalars().all())
    if not mappings:
        return BulkApplyResult(created_count=0, skipped_existing_count=0)

    payment_method_ids = [m.payment_method_id for m in mappings]
    existing = await _existing_pairs(session, merchant_ids=merchant_ids, payment_method_ids=payment_method_ids)

    to_create: list[MerchantPaymentMethod] = []
    skipped = 0
    for merchant_id in merchant_ids:
        for mapping in mappings:
            if (merchant_id, mapping.payment_method_id) in existing:
                skipped += 1
                continue
            to_create.append(
                MerchantPaymentMethod(
                    merchant_id=merchant_id,
                    payment_method_id=mapping.payment_method_id,
                    personal_rate=template.default_personal_rate,
                    transaction_min_limit=template.transaction_min_limit,
                    transaction_max_limit=template.transaction_max_limit,
                    test_mode=template.test_mode,
                    only_admin_configure=template.only_admin_configure,
                )
            )

    if to_create:
        session.add_all(to_create)
        await session.flush()

    # Deliberately NOT invalidating the Redis method cache here — see the
    # module docstring. Matches source's `bulk_create` bypassing `save()`.
    return BulkApplyResult(created_count=len(to_create), skipped_existing_count=skipped)


async def apply_selected_methods_to_merchants(
    session: AsyncSession,
    *,
    merchant_ids: list[int],
    currency_id: int,
    direction: str,
    method_ids: list[int],
    personal_rate: Decimal,
    transaction_min_limit: Decimal | None,
    transaction_max_limit: Decimal | None,
    test_mode: bool,
    only_admin_configure: bool,
) -> BulkApplyResult:
    if not merchant_ids:
        raise ValueError("Не выбрано ни одного мерчанта")
    if not method_ids:
        raise ValueError("Не выбрано ни одного платёжного метода")

    result = await session.execute(
        select(PaymentMethod.id).where(
            PaymentMethod.id.in_(method_ids),
            PaymentMethod.currency_id == currency_id,
            PaymentMethod.direction == direction,
        )
    )
    matched_method_ids = [row[0] for row in result.all()]
    if not matched_method_ids:
        return BulkApplyResult(created_count=0, skipped_existing_count=0)

    existing = await _existing_pairs(session, merchant_ids=merchant_ids, payment_method_ids=matched_method_ids)

    to_create: list[MerchantPaymentMethod] = []
    skipped = 0
    for merchant_id in merchant_ids:
        for payment_method_id in matched_method_ids:
            if (merchant_id, payment_method_id) in existing:
                skipped += 1
                continue
            to_create.append(
                MerchantPaymentMethod(
                    merchant_id=merchant_id,
                    payment_method_id=payment_method_id,
                    personal_rate=personal_rate,
                    transaction_min_limit=transaction_min_limit,
                    transaction_max_limit=transaction_max_limit,
                    test_mode=test_mode,
                    only_admin_configure=only_admin_configure,
                )
            )

    if to_create:
        session.add_all(to_create)
        await session.flush()

    # Same deliberate non-invalidation as `apply_template_to_merchants`.
    return BulkApplyResult(created_count=len(to_create), skipped_existing_count=skipped)


async def validate_merchant_ids(session: AsyncSession, merchant_ids: list[int]) -> None:
    """Not part of source (which trusts Django admin's own queryset scoping)
    but a cheap real check worth adding here since this API accepts raw ids
    directly rather than a server-rendered `<select multiple>`."""
    if not merchant_ids:
        raise ValueError("Не выбрано ни одного мерчанта")
    result = await session.execute(select(Merchant.id).where(Merchant.id.in_(merchant_ids)))
    found = {row[0] for row in result.all()}
    missing = set(merchant_ids) - found
    if missing:
        raise RecordNotFoundError(f"Merchant(s) not found: {sorted(missing)}")
