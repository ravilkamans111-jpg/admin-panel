from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.deps import CurrentUser, require_role
from app.core.rbac import role_at_least
from app.core.roles import BrandRole


def test_role_at_least_ordering():
    assert role_at_least("viewer", BrandRole.VIEWER) is True
    assert role_at_least("viewer", BrandRole.OPERATOR) is False
    assert role_at_least("operator", BrandRole.OPERATOR) is True
    assert role_at_least("operator", BrandRole.VIEWER) is True
    assert role_at_least("superadmin", BrandRole.BRAND_ADMIN) is True
    assert role_at_least("brand_admin", BrandRole.SUPERADMIN) is False


def test_role_at_least_rejects_unknown_role():
    assert role_at_least("not-a-real-role", BrandRole.VIEWER) is False


async def test_require_role_allows_sufficient_role():
    checker = require_role(BrandRole.OPERATOR)
    user = CurrentUser(admin_user_id=1, brand_id="ampay", role="operator")
    result = await checker(current_user=user)
    assert result is user


async def test_require_role_rejects_insufficient_role():
    checker = require_role(BrandRole.OPERATOR)
    user = CurrentUser(admin_user_id=1, brand_id="ampay", role="viewer")
    with pytest.raises(HTTPException) as exc_info:
        await checker(current_user=user)
    assert exc_info.value.status_code == 403
