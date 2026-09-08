# Frontend — Brand Admin Panel

Next.js 14 (App Router) + TypeScript + Tailwind CSS. Talks to the FastAPI
backend in `../backend` — read that service's README for the API contract
this UI is built against.

## What's here

- **Two-step login** (`/login` → `/select-brand`): email+password against
  `/auth/login`, then a workspace picker for `/auth/select-brand`. Matches
  the product spec's "select brand → get brand-scoped JWT" flow.
- **Workspace switcher** (header dropdown, `src/components/WorkspaceSwitcher.tsx`):
  switches brands mid-session using the still-valid `pre_auth_token` kept in
  `sessionStorage` — no re-entering a password to hop between AmPay/RajaPay/
  Quiet Forest. The selected brand and both access/refresh tokens persist in
  `localStorage` so a page reload restores the same workspace.
- **Auth context + guard** (`src/lib/auth-context.tsx`): bootstraps from
  localStorage on load, silently calls `/auth/refresh` if only a refresh
  token survived, and redirects to `/login` if nothing is valid.
  `authedFetch` (`src/lib/api.ts`) retries once through `/auth/refresh` on
  any 401 before giving up.
- **Schema-driven generic admin** (`src/app/(app)/admin/[key]/page.tsx` +
  `[id]/page.tsx`, `src/components/DataTable.tsx`): fetches `/admin/schema`
  once (`src/lib/schema-context.tsx`), and renders every one of the ~28
  registered models' list/filter/search/sort/paginate/detail screens from
  that config — no per-model frontend code. A model's `notes` field (set on
  the backend for anything with embedded business logic not reproduced in
  this read-only view — e.g. Settlements, PaymentMethodCascade) renders as a
  dismissible warning banner at the top of that model's page.
- **Dashboard** (`src/app/(app)/dashboard/page.tsx`): stat cards + a
  transactions-by-status bar + a balances-by-currency table, from
  `/dashboard/summary`.
- Left nav grouped by Django app label (`personal_account_transaction`,
  `api_mediator`, etc.), matching how the source monoliths' admin.py files
  were organized.

~1,700 lines of TypeScript/TSX across pages, components, hooks and the
`lib/` API+auth+storage layer.

## Verified

- `npm run build` compiles clean, zero TypeScript errors.
- Full manual walkthrough in a real browser against the dockerized backend +
  Postgres + Vault stack: login → brand picker (all 3 brands, superadmin
  role) → dashboard (per-brand data) → Transactions table (search/filter/
  sort all functional) → workspace switch to RajaPay with no re-login →
  dashboard correctly shows RajaPay's isolated data → Settlements page shows
  its `notes` warning banner → transaction detail view renders every field.
- Docker build (`docker compose build frontend`) succeeds; the standalone
  Next.js output runs correctly as the `frontend` service in
  `../docker-compose.yml` on port 3000.

## Known gaps / follow-ups

- `npm audit` still reports high-severity advisories against Next.js 14.2.x
  (most GHSA ranges only close on a 15/16 major bump). Deliberately did not
  force that bump mid-build since it would need its own compatibility pass
  against the App Router code here — track as a dedicated follow-up, not
  something to silently absorb into this PR.
- Filter inputs on list pages are plain text/number fields for every
  `list_filter` field, including ones that are really foreign keys (e.g.
  `merchant_id`). The backend's `/admin/schema` doesn't carry enough
  metadata to render a friendly "pick a merchant by name" dropdown instead
  of a raw ID — a reasonable next step once the backend adds FK
  target/label info to the schema payload.
- No dedicated enhanced UI for `merchant-balances` beyond the generic table
  with monospace amount formatting — a fuller balances view (grouped by
  currency, computed totals inline) is a good candidate for the next design
  pass once real usage patterns are known.

## Running

Dev server (expects the backend at `NEXT_PUBLIC_API_BASE_URL`, default
`http://localhost:8000`):

```bash
cd frontend
npm install
npm run dev
```

Production build:

```bash
npm run build && npm run start
```

Via the full stack (recommended — brings up Postgres/Vault/backend too):

```bash
docker compose up -d
```

Then open `http://localhost:3000/login`. Bootstrap a superuser first if you
haven't (see the root README / `scripts/bootstrap_admin_user.py`).
