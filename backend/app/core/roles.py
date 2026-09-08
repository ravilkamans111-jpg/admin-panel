"""Brand-scoped role vocabulary and hierarchy.

Lives in `core` (not `models`) because it's a plain business concept that
both the DB layer (`app.models.control_plane.BrandAccess.role` uses this as
its column type) and the write-authorization layer
(`app.core.rbac`/`app.api.deps.require_role`) need — and `core` sits at the
bottom of the import-linter layer contract, so nothing above it can hand it
a dependency in the wrong direction.
"""

from __future__ import annotations

import enum


class BrandRole(str, enum.Enum):
    VIEWER = "viewer"  # read-only
    OPERATOR = "operator"  # can edit Transaction/Settlements etc. (write actions)
    BRAND_ADMIN = "brand_admin"  # reserved: manage BrandAccess for this brand
    SUPERADMIN = "superadmin"  # full cross-brand access, incl. superuser-only actions
