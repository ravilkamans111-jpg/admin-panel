"""initial control-plane schema

Revision ID: 0001
Revises:
Create Date: 2026-09-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    brand_role = sa.Enum("viewer", "operator", "brand_admin", "superadmin", name="brandrole")

    op.create_table(
        "admin_user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "brand_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "admin_user_id",
            sa.Integer(),
            sa.ForeignKey("admin_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("brand_id", sa.String(64), nullable=False),
        sa.Column("role", brand_role, nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("admin_user_id", "brand_id", name="uq_brand_access_user_brand"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "admin_user_id",
            sa.Integer(),
            sa.ForeignKey("admin_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("brand_id", sa.String(64), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("brand_access")
    op.drop_table("admin_user")
    sa.Enum(name="brandrole").drop(op.get_bind(), checkfirst=True)
