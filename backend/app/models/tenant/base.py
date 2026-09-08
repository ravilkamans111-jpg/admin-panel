"""Declarative base for tenant (brand) database models.

These models are mapped **read-only** against the exact schema of the three
source Django monoliths (table names, column names, precision — verified
against their actual `migrations/*.py`, not just admin.py prose). This
service never issues INSERT/UPDATE/DELETE against a tenant DB — see
`app.api.deps.get_tenant_session`, which opens every tenant session in
`SET TRANSACTION READ ONLY`.

Field coverage: every model that had an `admin.py` registration in the
monoliths' `personal_account_auth`, `personal_account_transaction`,
`api_mediator`, `api_client`, `conversion_statistic`, and `sandbox` apps.
`CompanyMethodStatistics` and `Appeal`/`AntiFraudBlockedMerchantUsers`
disputes admin were flagged in the migration research as either unregistered
or partially dead in the source admin.py — they are still mapped here (the
underlying tables are real) so they're available once/if the team confirms
they should be exposed.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class TenantBase(DeclarativeBase):
    pass
