# Brand Admin Panel

Unified, multi-tenant **read-only** admin panel for three payment-platform
brands (AmPay, RajaPay, quiet-forest) that currently run identical Django
monoliths against three separate databases.

## Status

**Read-only MVP: backend fully built, tested (unit + against a real
Postgres/Vault stack), and covers all 28 admin-registered models across the
three source monoliths.** No write/mutate path exists anywhere in this
service by design — see "Scope" below.

## Architecture

- **One FastAPI service, three tenant databases.** The three monoliths are
  already deployed as one DB per brand (confirmed by reading their actual
  migrations, not just admin.py) — this service keeps that shape rather than
  consolidating into a shared schema. `app/tenancy/registry.py` resolves
  `brand_id` -> a cached async SQLAlchemy engine per brand.
- **A new control-plane database** for staff identity (`AdminUser`),
  per-brand access grants (`BrandAccess`), and an audit log. None of this
  existed in the source monoliths — each one authenticates admin staff
  independently via Django's own session-based admin login, with no concept
  of a user spanning brands. This is genuinely new infrastructure.
- **Two-step JWT login**, per the product spec: `POST /auth/login` verifies
  credentials and returns the caller's available brands; `POST
  /auth/select-brand` issues a brand-scoped access+refresh token pair. Every
  subsequent request's JWT carries `brand_id`, which `app/tenancy/deps.py`
  uses to pick the tenant DB session — opened in `SET TRANSACTION READ ONLY`
  as a belt-and-suspenders guarantee on top of the generic engine only ever
  issuing SELECTs.
- **Secrets via HashiCorp Vault** (KV v2), matching the principle already
  used by the three monoliths. Brand DB credentials are namespaced by
  `brand_id` as a Vault *path* segment (`brand-config/<brand_id>/db`), not a
  suffixed key — the migration research on the source monoliths found a real
  cross-brand secret leak caused by exactly that suffixed-key convention
  (RajaPay's settings.py silently aliasing `AMPAY_EMAIL` to its own value).
  No hardcoded fallback secrets exist anywhere: a missing required secret
  fails startup rather than degrading to a weak default (the monoliths had
  several of these — `SECRET_KEY="django_secret_key"`, a hardcoded bearer
  token — see the migration research in `docs/` if you want the paper trail).
- **A generic, config-driven admin engine** (`app/admin_engine/`) instead of
  28 hand-written route sets: every model gets an `AdminModelConfig`
  (list_display/list_filter/search_fields/default_ordering), and one router
  serves list/detail/filter/search/pagination for all of them, plus a
  `/admin/schema` endpoint the frontend uses to build its entire nav and
  table UI without per-model frontend code.

## Scope — read this before treating anything here as a write API

The three source monoliths are not simple CRUD apps: real balance-mutating
business logic (multi-service balance state machines, a settlement→
transaction cross-sync, Redis cache invalidation, Celery-dispatched
callbacks) lives inside Django `admin.py` `save_model()` overrides and
`Model.save()` methods, threaded through `from_admin=True` kwargs into a
`business_logic/services/` layer. Reading `transaction_save.py` directly
during this build turned up a ~200-line balance state machine branching on
`(direction) × (new_status) × (old_status)` for a single save — that class
of logic across ~15 service files was judged too large and too
safety-critical to port in one pass, so **this service deliberately does
not implement any of it.** Every write action from the original Django
admin stays exactly where it is; this panel only reads.

Model configs in `app/admin_engine/registry.py` carry a `notes` field
wherever the source `admin.py` had business logic beyond display (e.g.
Settlements, PaymentMethodCompany, PaymentMethodCascade) — read those before
assuming a model is "just a table."

### Extending this into a write-capable admin

Do not add `POST`/`PUT`/`DELETE` routes to the generic admin engine as a
shortcut — it was built read-only on purpose. A safe write path needs, per
model with embedded logic: the real Django service (e.g.
`TransactionSaveService`, `SettlementSaveService`) read line-by-line and
reimplemented with equivalent unit test coverage for every
status/direction transition, not just "looks similar." Budget for that as
its own project phase, not a follow-up PR.

## Repository layout

```
backend/    FastAPI service (see backend/README below via `app/` docstrings)
frontend/   Next.js admin UI (workspace switcher, generic tables, dashboard)
scripts/    seed_tenant_db.py (throwaway local Postgres schema+data),
            bootstrap_admin_user.py (create the first superuser)
docker-compose.yml   full local stack: control-plane PG, 3 tenant PGs,
                      Vault (dev mode), backend, frontend
```

## Running locally

```bash
docker compose up -d control_plane_db ampay_db rajapay_db quiet_forest_db vault

cd backend
uv venv && uv pip install -e ".[dev]"

# Point Vault, write dev secrets (see the Vault CLI commands used during
# this build for an example — admin-panel/auth and brand-config/<brand>/db
# KV-v2 paths), then:
uv run alembic -c alembic_control_plane.ini upgrade head
uv run python ../scripts/seed_tenant_db.py ampay
uv run python ../scripts/bootstrap_admin_user.py you@example.com 'a-strong-password'
uv run uvicorn app.main:app --reload
```

Or, for a quick local run without Vault: copy `backend/.env.example` to
`backend/.env` (`USE_LOCAL_ENV_SECRETS=true`), start just the Postgres
services, and skip the Vault steps above.

Run the test suite (uses in-memory SQLite, no external services needed):

```bash
cd backend && uv run pytest
```

Frontend: see `frontend/README.md`.

## What was verified in this build

- 14/14 backend unit tests pass (auth flow, tenant filtering/search/
  pagination, permission checks, 404/400 handling) against SQLite.
- Full stack smoke-tested against **real** Postgres + Vault (not mocked):
  Vault KV bootstrap → login → brand selection → `SET TRANSACTION READ ONLY`
  session → `/admin/transactions` → `/dashboard/summary`, all 28 models
  present in `/admin/schema`.
- `ruff check` clean.

What was **not** verified: this has not been run against a copy of a real
brand's actual database — the tenant schema was hand-derived from the three
monoliths' Django migration files (not guessed from admin.py prose), but a
migration drift between what was read here and a brand's current live
schema is still possible. Confirm column-for-column against a staging
snapshot of each brand's DB before pointing this at production data.
