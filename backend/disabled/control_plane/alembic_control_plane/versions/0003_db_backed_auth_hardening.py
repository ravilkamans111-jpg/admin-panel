"""db-backed auth hardening: lockout/session columns, restore audit_log FK

Removes the last trace of the hardcoded superuser: orphaned audit rows that
referenced its sentinel id (-1) are detached, and the foreign key dropped in
0002 is restored so `audit_log.admin_user_id` is enforced again.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05

"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("admin_user", sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("admin_user", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("admin_user", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("admin_user", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("admin_user", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))

    op.execute(
        "UPDATE audit_log SET admin_user_id = NULL "
        "WHERE admin_user_id IS NOT NULL AND admin_user_id NOT IN (SELECT id FROM admin_user)"
    )
    with op.batch_alter_table("audit_log") as batch_op:
        batch_op.create_foreign_key(
            "audit_log_admin_user_id_fkey", "admin_user", ["admin_user_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_log") as batch_op:
        batch_op.drop_constraint("audit_log_admin_user_id_fkey", type_="foreignkey")
    op.drop_column("admin_user", "token_version")
    op.drop_column("admin_user", "password_changed_at")
    op.drop_column("admin_user", "last_login_at")
    op.drop_column("admin_user", "locked_until")
    op.drop_column("admin_user", "failed_login_attempts")
