# Brand Admin Panel

One admin panel for the three payment platforms — **AmPay, RajaPay, quiet-forest** — in
place of their three Django admins. It reads and writes each brand's own PostgreSQL
database directly (one database per brand, exactly as the monoliths run today).

- `backend/` — FastAPI service (SQLAlchemy async). The admin engine is driven by a
  registry of models (`app/registry/admin_models.py`); money-moving operations
  (transactions, settlements, balances, antifraud, payment-method configs, cascades,
  merchant bulk actions) are ports of the monoliths' `save()` logic with their own services
  and tests.
- `frontend/` — Next.js UI (workspace switcher, lists with filters/search/export, forms).
- `DEPLOY.md` — how to run it in production and every value that has to be filled in.

## How it works

- **Login is the Django admin login.** Staff sign in with their username and password from
  each brand's `auth_user` (PBKDF2); every brand where they are an active staff member
  becomes a workspace. Role per brand comes from Django: superuser → `superadmin`, any
  add/change/delete permission → `operator`, otherwise `viewer`. There is no user database
  of our own. Users are managed on the "Users" page (Django-style add/change forms) or in
  the monolith's admin.
- **Sessions:** two-step JWT login (`/auth/login` → `/auth/select-brand`), 15-minute access
  and 7-day refresh tokens. Refresh re-reads `auth_user`, so deactivating a user, changing
  their password or permissions in either admin takes effect at the next refresh.
- **Brute force:** 5 failed attempts lock a login name for 15 minutes (counter in Redis, in
  memory if Redis is down). Per-IP limits belong on the reverse proxy.
- **Audit:** every write goes to the `audit` logger (JSON) and to the brand's own
  `django_admin_log`, attributed to the staff member's real `auth_user.id`, so changes show
  up in the monolith's admin History.
- **Secrets** live in HashiCorp Vault, laid out exactly like the monoliths' `vault_loader.py`
  (userpass login, KV v2 mount `backend`, paths `settings` / `urls` / `api_keys`, the
  monoliths' own key names). Nothing secret is stored in the repository or the images.
- **Side effects stay in the monolith:** merchant callbacks are enqueued as the monolith's
  own Celery task (its workers send them); cache invalidation writes to the brand's Redis.

## Development

```bash
cd backend && uv venv && uv pip install -e ".[dev]" && uv run pytest     # 140+ tests, no services needed
cd frontend && npm ci && NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

`docker-compose.prod.yml` is the production stack. A local stack (`docker-compose.yml`: throwaway Postgres per
brand, a dev Vault, Redis) and the seed/test-user scripts live in the git repository only, not in the production archive.

## Layout

```
backend/app/api            HTTP handlers (thin)
backend/app/services       business logic: auth, balances, settlements, callbacks, audit, ...
backend/app/repositories   data access (generic admin queries, Django auth tables)
backend/app/registry       the model registry that drives lists, forms, filters
backend/app/db, models     engines/sessions per brand; SQLAlchemy models of the monoliths' tables
backend/app/core           config & Vault, JWT, Django password hashes, roles
```

Import layering (`api → services → repositories → registry → db → models → core`) is enforced
by import-linter in CI (`lint-imports`).
