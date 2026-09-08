"""Port of `MerchantBalanceAdmin.refresh_balances` (`personal_account_auth/admin.py`)
— a raw-SQL bulk recompute of every `MerchantBalance` row from the ledger
(`transaction` table), reachable only via a custom admin URL
(`/admin/personal_account_auth/merchantbalance/refresh-balances/`) gated on
`request.user.is_superuser`, not a per-model action button.

Source (verbatim SQL, `refresh_balances`):
```sql
UPDATE public.merchantbalance mb
SET
    balance = COALESCE(calc.balance, 0),
    blocked_balance_in = COALESCE(calc.blocked_balance_in, 0),
    blocked_balance_out = COALESCE(calc.blocked_balance_out, 0)
FROM (
    SELECT
        tr.merchant_id,
        pm.currency_id,
        SUM(CASE WHEN tr.direction = 'IN'  AND tr.status = 'SUCCESS'  THEN tr.amount_after_commission ELSE 0 END)
        - SUM(CASE WHEN tr.direction = 'OUT' AND tr.status = 'SUCCESS'  THEN tr.amount_after_commission ELSE 0 END)
        - SUM(CASE WHEN tr.direction = 'OUT' AND tr.status = 'ACCEPTED' THEN tr.amount_after_commission ELSE 0 END)
            AS balance,
        SUM(CASE WHEN tr.direction = 'IN'  AND tr.status = 'ACCEPTED' THEN tr.amount_after_commission ELSE 0 END)
            AS blocked_balance_in,
        SUM(CASE WHEN tr.direction = 'OUT' AND tr.status = 'ACCEPTED' THEN tr.amount_after_commission ELSE 0 END)
            AS blocked_balance_out
    FROM public.transaction tr
    JOIN public.payment_method_company pa ON tr.payment_method_company_id = pa.id
    JOIN public.payment_method pm ON pa.payment_method_id = pm.id
    GROUP BY tr.merchant_id, pm.currency_id
) calc
WHERE mb.merchant_id = calc.merchant_id
AND mb.currency_id = calc.currency_id;
```

Ported verbatim, including two easy-to-miss quirks:

1. **This is an `UPDATE ... FROM`, not an upsert.** A `(merchant_id, currency_id)`
   pair with transactions but NO pre-existing `MerchantBalance` row is
   silently skipped — nothing is created. Conversely, a `MerchantBalance`
   row whose `(merchant_id, currency_id)` has NO matching `calc` row (no
   transactions at all, ever, for that pair) is also left completely
   untouched — NOT reset to zero, despite the `COALESCE(..., 0)` in the
   `SET` clause; that `COALESCE` only guards a NULL SUM (a calc row that
   matched by merchant/currency but had e.g. no OUT-ACCEPTED rows), it
   never fires for a merchant/currency pair the query never produces a
   `calc` row for at all, because the `WHERE` join predicate excludes those
   `mb` rows from the update entirely. Both behaviors are preserved as-is —
   no `INSERT ... ON CONFLICT`, no zeroing-out of untouched rows added here.

2. **Every currency, every merchant, unscoped by anything else** — no
   status/date filter beyond what's in the `CASE` expressions themselves.
   A brand with a large `transaction` table pays for a full aggregate scan
   every time this runs; source has the exact same cost profile (no
   incremental/partial recompute), so this is not a regression to fix here.

`balance_usdt` / `insurance_balance_usdt` / `blocked_balance_usdt_in` /
`blocked_balance_usdt_out` are NOT touched by this SQL in source either —
those are maintained by a different (not-yet-ported) exchange-rate path.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_REFRESH_SQL = text("""
    UPDATE merchantbalance AS mb
    SET
        balance = COALESCE(calc.balance, 0),
        blocked_balance_in = COALESCE(calc.blocked_balance_in, 0),
        blocked_balance_out = COALESCE(calc.blocked_balance_out, 0)
    FROM (
        SELECT
            tr.merchant_id,
            pm.currency_id,
            SUM(CASE WHEN tr.direction = 'IN'  AND tr.status = 'SUCCESS'  THEN tr.amount_after_commission ELSE 0 END)
            - SUM(CASE WHEN tr.direction = 'OUT' AND tr.status = 'SUCCESS'  THEN tr.amount_after_commission ELSE 0 END)
            - SUM(CASE WHEN tr.direction = 'OUT' AND tr.status = 'ACCEPTED' THEN tr.amount_after_commission ELSE 0 END)
                AS balance,
            SUM(CASE WHEN tr.direction = 'IN'  AND tr.status = 'ACCEPTED' THEN tr.amount_after_commission ELSE 0 END)
                AS blocked_balance_in,
            SUM(CASE WHEN tr.direction = 'OUT' AND tr.status = 'ACCEPTED' THEN tr.amount_after_commission ELSE 0 END)
                AS blocked_balance_out
        FROM "transaction" tr
        JOIN payment_method_company pa ON tr.payment_method_company_id = pa.id
        JOIN payment_method pm ON pa.payment_method_id = pm.id
        GROUP BY tr.merchant_id, pm.currency_id
    ) calc
    WHERE mb.merchant_id = calc.merchant_id
    AND mb.currency_id = calc.currency_id
""")


async def refresh_all_merchant_balances(session: AsyncSession) -> int:
    """Runs the recompute and returns the number of `MerchantBalance` rows
    actually updated (matches source's implicit "Балансы обновлены" with no
    count — this port surfaces the count since it's cheap and more useful
    than a bare success message)."""
    result = await session.execute(_REFRESH_SQL)
    return result.rowcount if result.rowcount is not None and result.rowcount >= 0 else 0
