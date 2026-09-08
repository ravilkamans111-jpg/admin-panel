"""drop FK on audit_log.admin_user_id

The hardcoded superuser (`app.services.auth_service.HARDCODED_SUPERUSER_ID`)
never has a matching `admin_user` row by design — every write action logs
to `audit_log` with the acting user's id, so a FK here would reject every
audit write made while authenticated as that identity. `admin_user_id`
stays a plain nullable integer: still useful for correlation, no longer
enforced against `admin_user.id`.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-08

"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("audit_log") as batch_op:
        batch_op.drop_constraint("audit_log_admin_user_id_fkey", type_="foreignkey")


def downgrade() -> None:
    with op.batch_alter_table("audit_log") as batch_op:
        batch_op.create_foreign_key(
            "audit_log_admin_user_id_fkey", "admin_user", ["admin_user_id"], ["id"], ondelete="SET NULL"
        )
