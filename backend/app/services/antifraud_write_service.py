"""Port of `AntiFraudBlockedMerchantUsersAdmin.save_model` ->
`AntiFraudBlockedMerchantUsers.save(from_admin=True, old_value=BlockListDTO(...))`.

Source (verbatim, from `personal_account_transaction/models.py`):

```python
def save(self, *args, **kwargs):
    if kwargs.pop('from_admin', False):
        old_value = kwargs.pop('old_value', None)
        if old_value:
            if self.second_chance and not old_value.second_chance:
                self.second_chance_counter += 1
                self.second_chance_date = timezone.localtime()
            if self.permanent_ban and not old_value.permanent_ban:
                self.ban_date = timezone.localtime()
            if not self.second_chance and old_value.second_chance:
                self.ban_date = timezone.localtime()
        else:
            self.merchant_name = str(self.merchant)
            self.ban_date = timezone.localtime()
    else:
        self.merchant_name = str(self.merchant)
    super().save(*args, **kwargs)
```

This write path only ever edits an existing row (no create — see registry:
`editable_fields` is set, `creatable` is not, matching the Transaction write
path's scoping decision), so `old_value` is always present here — only the
`if old_value:` branch applies. `merchant_name` is deliberately NOT
recomputed on an admin edit per source (only on the `else` fallback paths,
neither of which this write path reaches) — a genuinely surprising
consequence: if you edit `merchant` via this form, `merchant_name` keeps
showing the OLD merchant's name until some other save path touches the row.
Preserved bug-for-bug, not "fixed."

`str(merchant)` (source, `personal_account_auth/models.py`):
`f'{user.username}: {name}'` if `name` else `user.username` — only relevant
to the two branches this write path doesn't reach, kept here for reference
should create support be added later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RecordNotFoundError
from app.models.tenant import AntiFraudBlockedMerchantUsers
from app.registry.admin_models import get_config
from app.repositories.admin_repository import row_to_dict

_config = get_config("antifraud-blocks")
assert _config is not None, "antifraud-blocks must be registered in app.registry.admin_models"
EDITABLE_FIELDS = tuple(_config.editable_fields)


@dataclass(frozen=True, slots=True)
class _BlockSnapshot:
    second_chance: bool
    permanent_ban: bool


async def update_antifraud_block(
    session: AsyncSession, *, pk: int, values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = await session.execute(
        select(AntiFraudBlockedMerchantUsers).where(AntiFraudBlockedMerchantUsers.id == pk).with_for_update()
    )
    block = result.scalar_one_or_none()
    if block is None:
        raise RecordNotFoundError(f"AntiFraudBlockedMerchantUsers {pk} not found")

    before = row_to_dict(block)
    old = _BlockSnapshot(second_chance=block.second_chance, permanent_ban=block.permanent_ban)

    unknown = [f for f in values if f not in EDITABLE_FIELDS]
    if unknown:
        raise ValueError(f"Not editable on AntiFraudBlockedMerchantUsers: {unknown}")
    for field_name, value in values.items():
        setattr(block, field_name, value)

    now = datetime.now(UTC)
    if block.second_chance and not old.second_chance:
        block.second_chance_counter += 1
        block.second_chance_date = now
    if block.permanent_ban and not old.permanent_ban:
        block.ban_date = now
    if not block.second_chance and old.second_chance:
        block.ban_date = now

    await session.flush()
    after = row_to_dict(block)
    return before, after
