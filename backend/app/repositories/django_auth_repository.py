"""Reads Django's own auth tables in a brand database (`auth_user`,
`auth_permission`, groups) — the same data the monolith's admin login uses.

Raw SQL on purpose: `auth_user.password` is deliberately not mapped on the ORM
model (`DjangoAuthUser`), so it can never leak through the generic admin list
and detail views. Read-only; nothing here writes to a brand's auth tables.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class DjangoStaffUser:
    id: int
    username: str
    password_hash: str
    is_active: bool
    is_staff: bool
    is_superuser: bool

    @property
    def can_use_admin(self) -> bool:
        """Django admin's own gate (`AdminSite.has_permission`): active AND staff."""
        return self.is_active and self.is_staff


_COLUMNS = "id, username, password, is_active, is_staff, is_superuser"


def _row_to_user(row) -> DjangoStaffUser:
    return DjangoStaffUser(
        id=row.id, username=row.username, password_hash=row.password or "",
        is_active=bool(row.is_active), is_staff=bool(row.is_staff), is_superuser=bool(row.is_superuser),
    )


async def get_user_by_username(session: AsyncSession, username: str) -> DjangoStaffUser | None:
    """Exact-match lookup, like Django's `ModelBackend` (`get_by_natural_key`)."""
    row = (await session.execute(text(f"SELECT {_COLUMNS} FROM auth_user WHERE username = :u"), {"u": username})).first()
    return _row_to_user(row) if row else None


async def get_user_by_id(session: AsyncSession, user_id: int) -> DjangoStaffUser | None:
    row = (await session.execute(text(f"SELECT {_COLUMNS} FROM auth_user WHERE id = :i"), {"i": user_id})).first()
    return _row_to_user(row) if row else None


async def get_permission_codenames(session: AsyncSession, user_id: int) -> set[str]:
    """Codenames granted directly or through groups (`add_x`, `change_x`, ...)."""
    rows = await session.execute(
        text(
            """
            SELECT p.codename FROM auth_permission p
            JOIN auth_user_user_permissions up ON up.permission_id = p.id WHERE up.user_id = :u
            UNION
            SELECT p.codename FROM auth_permission p
            JOIN auth_group_permissions gp ON gp.permission_id = p.id
            JOIN auth_user_groups ug ON ug.group_id = gp.group_id WHERE ug.user_id = :u
            """
        ),
        {"u": user_id},
    )
    return {r[0] for r in rows.all()}
