"""Port of the three REACHABLE cache-clear admin actions in `api_mediator/admin.py`:
`clear_cache_by_currency` (on `PaymentMethodCompanyAdmin`), `clear_cache_by_merchant`
(on `MerchantPaymentMethodAdmin`), and `clear_all_payment_methods_cache` (registered
on both admins). These are explicit "nuke the cache" buttons distinct from the
automatic invalidation `PaymentMethodCompany.save()` / `MerchantPaymentMethod.save()`
already run (see `app.services.payment_method_write_service`) — an operator reaches
for these when they suspect the cache is stale for reasons a normal save wouldn't
have caught (e.g. after a bulk DB fix outside the admin, or a Redis hiccup).

Source (verbatim, `clear_cache_by_currency`):
```python
def clear_cache_by_currency(modeladmin, request, queryset):
    currencies = set()
    for payment_method_company in queryset:
        currency = payment_method_company.payment_method.currency.iso_code
        currencies.add(currency)
        invalidate_cache(currency=currency)  # busts ALL methods of this currency
    ...
```
Note the docstring's own warning: "Сбрасывает кеш для ВСЕХ методов валют выбранных
компаний (не только выбранные!)" — selecting ONE `PaymentMethodCompany` row busts
the cache for every merchant's cached method list in that row's currency, not just
that one company. Ported with the same blast radius, not narrowed.

Source (verbatim, `clear_cache_by_merchant`):
```python
def clear_cache_by_merchant(modeladmin, request, queryset):
    merchants = set()
    for merchant_method in queryset:
        merchant_id = merchant_method.merchant.id
        merchants.add((merchant_id, merchant_method.merchant.name))
        invalidate_cache(merchant_id=merchant_id)
    ...
```
Per-merchant, not per-row — selecting multiple rows for the same merchant just
invalidates that merchant once (idempotent), which this port preserves by
deduplicating merchant_ids before invalidating.

Source (verbatim, `clear_all_payment_methods_cache`):
```python
def clear_all_payment_methods_cache(modeladmin, request, queryset):
    invalidate_cache()  # no args = full reset, queryset is ignored entirely
```
Deliberately ignores whatever was selected — a full reset regardless of scope.
Ported the same way: `clear_all_payment_methods_cache` below takes no selection.

**NOT ported**: `clear_cache_by_method_currency`, defined in source right next to
these three (`api_mediator/admin.py`, same file) but never wired into either
ModelAdmin's `actions` list — confirmed by grep across the file, only
`clear_cache_by_currency`/`clear_all_payment_methods_cache` are registered on
`PaymentMethodCompanyAdmin` and only `clear_cache_by_merchant`/
`clear_all_payment_methods_cache` on `MerchantPaymentMethodAdmin`. It is dead code
in source, unreachable from the admin UI — not a gap in this port.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RecordNotFoundError
from app.models.tenant import Currency, Merchant, MerchantPaymentMethod, PaymentMethod, PaymentMethodCompany
from app.services.cache_invalidation import safe_invalidate_cache


async def clear_cache_by_currency(
    session: AsyncSession, *, brand_id: str, payment_method_company_ids: list[int]
) -> list[str]:
    """Returns the sorted, de-duplicated list of currency iso_codes whose
    cache was busted — for the confirmation message the frontend shows."""
    if not payment_method_company_ids:
        raise ValueError("Не выбрано ни одной записи")

    result = await session.execute(
        select(Currency.iso_code)
        .join(PaymentMethod, PaymentMethod.currency_id == Currency.id)
        .join(PaymentMethodCompany, PaymentMethodCompany.payment_method_id == PaymentMethod.id)
        .where(PaymentMethodCompany.id.in_(payment_method_company_ids))
        .distinct()
    )
    iso_codes = sorted({row[0] for row in result.all()})
    if not iso_codes:
        raise RecordNotFoundError("Ни одна из выбранных записей не найдена")

    for iso_code in iso_codes:
        await safe_invalidate_cache(brand_id, currency=iso_code)
    return iso_codes


async def clear_cache_by_merchant(
    session: AsyncSession, *, brand_id: str, merchant_payment_method_ids: list[int]
) -> list[dict]:
    """Returns the sorted (by name) list of {"id", "name"} merchants whose
    cache was busted."""
    if not merchant_payment_method_ids:
        raise ValueError("Не выбрано ни одной записи")

    result = await session.execute(
        select(Merchant.id, Merchant.name)
        .join(MerchantPaymentMethod, MerchantPaymentMethod.merchant_id == Merchant.id)
        .where(MerchantPaymentMethod.id.in_(merchant_payment_method_ids))
        .distinct()
    )
    merchants = sorted(({"id": row[0], "name": row[1]} for row in result.all()), key=lambda m: m["name"])
    if not merchants:
        raise RecordNotFoundError("Ни одна из выбранных записей не найдена")

    for merchant in merchants:
        await safe_invalidate_cache(brand_id, merchant_id=merchant["id"])
    return merchants


async def clear_all_payment_methods_cache(brand_id: str) -> None:
    """Full reset — ignores any selection, matches source exactly. No DB
    session needed since there's nothing to look up."""
    await safe_invalidate_cache(brand_id)
