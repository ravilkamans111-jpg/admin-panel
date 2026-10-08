# Deploying to production

The service is stateless: two containers (API + UI). Brand databases, Redis, the Celery
broker and Vault already exist — you only point the service at them.

## 1. Prerequisites

- Network access from the API container to: Vault, each brand's PostgreSQL, each brand's
  Redis and Celery broker (the monolith's), and — for the Telegram settlement callbacks — the bot URL.
- A reverse proxy with TLS in front (the compose file publishes `127.0.0.1:8000` for the API
  and `127.0.0.1:3000` for the UI). Restrict the panel to your office/VPN network and rate-limit
  `POST /auth/login` per IP there.
- **A dedicated PostgreSQL role per brand** (do not reuse the monolith's superuser): `SELECT` on
  the tables of the brand schema, `INSERT/UPDATE/DELETE` on the tables the panel edits, and
  `INSERT` on `django_admin_log`; plus `SELECT/INSERT/UPDATE` on `auth_user`,
  `auth_user_groups`, `auth_user_user_permissions` (the Users page).

## 2. Vault

The service logs in with `VAULT_USERNAME` / `VAULT_PASSWORD` (userpass) and reads the KV-v2 mount
`VAULT_MOUNT` (default `backend`), paths `settings`, `urls`, `api_keys` and `admin_panel` (merged,
later wins). Give its policy **read-only** access to that mount.

A key is looked up brand-suffixed first (`HOST_AMPAY`) and then plain (`DB_HOST`), so one mount can
serve all brands with suffixes (`AMPAY`, `RAJAPAY`, `QUIET_FOREST`), or each brand can keep its own
mount (set `VAULT_MOUNT_<BRAND>`), as the monoliths do.

| Needed | Keys tried (AMPAY shown) | Required |
|---|---|---|
| DB host | `HOST_AMPAY`, `DB_HOST_AMPAY`, `HOST`, `DB_HOST` | yes |
| DB port | `DB_PORT_AMPAY`, `DB_PORT` (default 5432) | |
| DB name / user / password | `POSTGRES_DB[_AMPAY]`, `POSTGRES_USER[_AMPAY]`, `POSTGRES_PASSWORD[_AMPAY]` | yes |
| Redis | `REDIS_URL[_AMPAY]`, or `REDIS_HOST[_AMPAY]` + `REDIS_PORT` (6379) + `REDIS_DB` (0) | yes |
| Celery broker (the monolith's) | `CELERY_BROKER_URL[_AMPAY]`, `CELERY_RESULT_BACKEND[_AMPAY]` | broker: yes |
| Admin merchant public key | `ADMIN_PUBLIC_KEY[_AMPAY]` | needed for FROM_PARTNER / TO_PARTNER settlements |
| Brand timezone (statistics days) | `APP_TIMEZONE[_AMPAY]` (default UTC) | set it |
| Telegram bot callback URL | `BOT_CALLBACK_URL[_AMPAY]` | for the settlement → Telegram action |
| JWT signing key | `JWT_SECRET_KEY` (path `admin_panel` or `settings`), 32+ random characters | yes |
| JWT tuning | `JWT_ALGORITHM` (HS256), `ACCESS_TOKEN_EXPIRE_MINUTES` (15), `REFRESH_TOKEN_EXPIRE_DAYS` (7) | |

Generate the JWT key with `openssl rand -hex 32`. Placeholders and keys shorter than 32 characters are
rejected at startup, as is a missing required value.

## 3. Configure and start

```bash
cp .env.production.example .env        # NEXT_PUBLIC_API_BASE_URL, CORS_ALLOW_ORIGINS, VAULT_*
docker compose -f docker-compose.prod.yml up -d --build
curl -s http://127.0.0.1:8000/health    # {"status":"ok"}
```

`NEXT_PUBLIC_API_BASE_URL` is baked into the UI image at build time — rebuild the frontend if the
public API address changes.

## 4. First login

There are no built-in accounts. Sign in with an existing staff user of the monolith (`is_staff`;
`is_superuser` for full rights). Staff with several brands get one workspace per brand.

## 5. Verify on staging before opening to users

1. Log in with a superuser, an operator-level and a viewer-level account; check the role shown per brand.
2. Edit a test transaction's status ACCEPTED → SUCCESS and DECLINED in the monolith and in the panel on
   copies of the same data; compare merchant/company balances and the frozen (blocked) amounts.
3. Create a settlement of each type on a test merchant.
4. "Send callbacks" on a test transaction: confirm the monolith's Celery worker receives
   `api_mediator.business_logic.services.callbacks.send_message_to_merchant`.
5. Change a payment-method-company priority and confirm the brand's Redis method cache is invalidated.
6. Check `django_admin_log` shows the panel's changes under the right user.
7. Run the list pages (transactions, settlements) against the production-size copy and look at the slow-query log.

## Operations notes

- Logs: the `audit` logger writes one JSON line per write action to stdout — ship it with the rest.
- The dashboard totals are cached for 60 s and filter choice lists for 5 min per process.
- The service never alters a brand's schema and never creates tables.
