# Disabled: control-plane auth + staff management

Authentication now mirrors the monoliths' Django admin: staff log in with their
brand's own `auth_user` credentials, no extra database (see
`backend/app/services/auth_service.py`). The previous design — a dedicated
control-plane Postgres with `admin_user` / `brand_access` / `audit_log`, lockout,
token versioning, a `/staff` management API and UI — is parked here, intact, so it
can be switched back on.

To re-enable: move `app/*` back under `backend/app/`, `alembic_control_plane*`
and `scripts/` under `backend/`, `tests/*` under `backend/tests/` (replacing the
new `test_auth.py`), restore `CONTROL_PLANE_DATABASE_URL`, the `control_plane_db`
and `migrate` services in `docker-compose.yml`, `frontend/disabled/staff-management/*`
under `frontend/src/app/(app)/`, the sidebar link, and the auth routes in
`app/api/auth.py` from `auth_service_control_plane.py`'s era (git history).
