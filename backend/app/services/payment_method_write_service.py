"""Port of `PaymentMethodCompany.save()` and `MerchantPaymentMethod.save()`
(`api_mediator/models.py`) — both models exist purely to configure which
payment methods are available (to a partner company, and to a merchant,
respectively), and both bust the same Redis-backed method-lookup cache on
every write. See `app.services.cache_invalidation` for the key-shape port.

Source (verbatim, `PaymentMethodCompany.save()`):

```python
def save(self, *args, **kwargs):
    cache_invalidation_fields = {
        "is_active", "transaction_min_limit", "transaction_max_limit",
        "daily_amount_limit", "daily_count_limit", "priority",
    }
    should_invalidate_cache = self.pk is None
    if not should_invalidate_cache:
        old_instance = PaymentMethodCompany.objects.get(pk=self.pk)
        for field in cache_invalidation_fields:
            if getattr(old_instance, field) != getattr(self, field):
                should_invalidate_cache = True
                break
    super().save(*args, **kwargs)
    if should_invalidate_cache:
        invalidate_cache(currency=self.payment_method.currency.iso_code)
```

Note `partner_rate`/`additional_commission`/`settlement_commission` are
NOT in `cache_invalidation_fields` — editing just the partner's rate does
NOT bust the cache in source. Preserved bug-for-bug: this write path only
ever edits an existing row (create isn't wired here, matching the
Transaction/AntiFraud write-path scoping), so the `self.pk is None` branch
never applies — only the diff-check branch is ported.

Source (verbatim, `MerchantPaymentMethod.save()`):

```python
def save(self, *args, **kwargs):
    super().save(*args, **kwargs)
    invalidate_cache(merchant_id=self.merchant.id)
```

Unconditional — every save busts the cache regardless of which field
changed, including a save that changes nothing balance/limit-relevant
(e.g. `no_callback`). Preserved as-is.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RecordNotFoundError
from app.models.tenant import Currency, MerchantPaymentMethod, PaymentMethod, PaymentMethodCompany
from app.registry.admin_models import get_config
from app.repositories.admin_repository import row_to_dict
from app.services import cascade_write_service
from app.services.cache_invalidation import safe_invalidate_cache

_pmc_config = get_config("payment-method-companies")
assert _pmc_config is not None, "payment-method-companies must be registered in app.registry.admin_models"
PMC_EDITABLE_FIELDS = tuple(_pmc_config.editable_fields)

_mpm_config = get_config("merchant-payment-methods")
assert _mpm_config is not None, "merchant-payment-methods must be registered in app.registry.admin_models"
MPM_EDITABLE_FIELDS = tuple(_mpm_config.editable_fields)

# Exact set from source's `cache_invalidation_fields` — deliberately does
# NOT include partner_rate/additional_commission/settlement_commission.
_PMC_CACHE_INVALIDATION_FIELDS = frozenset(
    {
        "is_active",
        "transaction_min_limit",
        "transaction_max_limit",
        "daily_amount_limit",
        "daily_count_limit",
        "priority",
    }
)


async def update_payment_method_company(
    session: AsyncSession, *, brand_id: str, pk: int, values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = await session.execute(select(PaymentMethodCompany).where(PaymentMethodCompany.id == pk).with_for_update())
    pmc = result.scalar_one_or_none()
    if pmc is None:
        raise RecordNotFoundError(f"PaymentMethodCompany {pk} not found")

    before = row_to_dict(pmc)
    old_values = {f: getattr(pmc, f) for f in _PMC_CACHE_INVALIDATION_FIELDS}

    unknown = [f for f in values if f not in PMC_EDITABLE_FIELDS]
    if unknown:
        raise ValueError(f"Not editable on PaymentMethodCompany: {unknown}")
    for field_name, value in values.items():
        setattr(pmc, field_name, value)

    should_invalidate = any(old_values[f] != getattr(pmc, f) for f in _PMC_CACHE_INVALIDATION_FIELDS)

    await session.flush()
    after = row_to_dict(pmc)

    if should_invalidate:
        payment_method = await session.get(PaymentMethod, pmc.payment_method_id)
        currency = await session.get(Currency, payment_method.currency_id) if payment_method else None
        if currency is not None:
            await safe_invalidate_cache(brand_id, currency=currency.iso_code)

    return before, after


async def update_merchant_payment_method(
    session: AsyncSession, *, brand_id: str, pk: int, values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = await session.execute(
        select(MerchantPaymentMethod).where(MerchantPaymentMethod.id == pk).with_for_update()
    )
    mpm = result.scalar_one_or_none()
    if mpm is None:
        raise RecordNotFoundError(f"MerchantPaymentMethod {pk} not found")

    before = row_to_dict(mpm)

    unknown = [f for f in values if f not in MPM_EDITABLE_FIELDS]
    if unknown:
        raise ValueError(f"Not editable on MerchantPaymentMethod: {unknown}")

    # Port of `MerchantPaymentMethod.clean()` — the cascade (if any, after
    # applying this edit) must target the SAME payment_method as this row.
    # payment_method_id itself isn't editable here (matches source locking
    # it down via the admin form), so only the incoming/existing cascade_id
    # needs checking against the row's existing payment_method_id.
    if "cascade_id" in values:
        await cascade_write_service.validate_merchant_payment_method_cascade(
            session, payment_method_id=mpm.payment_method_id, cascade_id=values["cascade_id"]
        )

    for field_name, value in values.items():
        setattr(mpm, field_name, value)

    await session.flush()
    after = row_to_dict(mpm)

    # Unconditional in source — every save busts the cache, no diff check.
    await safe_invalidate_cache(brand_id, merchant_id=mpm.merchant_id)

    return before, after
