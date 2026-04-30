"""paid repairs: orders.is_paid + users.is_paid_engineer + outsource request type

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-25
"""
from __future__ import annotations
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_orders_is_paid", "orders", ["is_paid"])

    op.add_column(
        "users",
        sa.Column("is_paid_engineer", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_users_is_paid_engineer", "users", ["is_paid_engineer"])

    # SQLite is lenient about enum-as-VARCHAR — adding a value requires no DDL.
    # Postgres needs ALTER TYPE; we run it conditionally so SQLite migrations stay clean.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE request_type_enum ADD VALUE IF NOT EXISTS 'outsource'")


def downgrade() -> None:
    op.drop_index("ix_users_is_paid_engineer", table_name="users")
    op.drop_column("users", "is_paid_engineer")
    op.drop_index("ix_orders_is_paid", table_name="orders")
    op.drop_column("orders", "is_paid")
    # Postgres can't drop enum value without recreating; leave as-is.
