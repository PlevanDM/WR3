"""orders.public_url

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-23
"""
from __future__ import annotations
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("public_url", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "public_url")
