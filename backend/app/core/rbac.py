"""Role hierarchy for write-action authorization.

Roles are ordered least → most privileged. `role_at_least` is what
`app.api.deps.require_role` uses to gate write endpoints — e.g. editing a
Transaction requires at least `operator`, while superuser-only actions
(matching the source's `refresh_balances` superuser check) require
`superadmin` explicitly.
"""

from __future__ import annotations

from app.core.roles import BrandRole

_ROLE_ORDER: dict[BrandRole, int] = {
    BrandRole.VIEWER: 0,
    BrandRole.OPERATOR: 1,
    BrandRole.BRAND_ADMIN: 2,
    BrandRole.SUPERADMIN: 3,
}


def role_at_least(role: str, minimum: BrandRole) -> bool:
    try:
        role_enum = BrandRole(role)
    except ValueError:
        return False
    return _ROLE_ORDER[role_enum] >= _ROLE_ORDER[minimum]
