"""initial

Revision ID: 0001
Revises:
Create Date: 2026-04-22

"""
from __future__ import annotations
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db.models import JSONType

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tg_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("username", sa.String(64)),
        sa.Column("full_name", sa.String(255)),
        sa.Column("role", _enum("role_enum", "admin", "reception", "engineer", "manager"), nullable=False),
        sa.Column("ro_employee_id", sa.BigInteger),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_tg_id", "users", ["tg_id"], unique=True)

    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("number", sa.String(64)),
        sa.Column("status_id", sa.BigInteger),
        sa.Column("status_name", sa.String(128)),
        sa.Column("client_name", sa.String(255)),
        sa.Column("device", sa.String(255)),
        sa.Column("serial", sa.String(255)),
        sa.Column("defect", sa.Text),
        sa.Column("created_at_ro", sa.DateTime(timezone=True)),
        sa.Column("last_activity_at", sa.DateTime(timezone=True)),
        sa.Column("assigned_engineer_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("has_photos", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("raw", JSONType),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_orders_number", "orders", ["number"])
    op.create_index("ix_orders_last_activity_at", "orders", ["last_activity_at"])
    op.create_index("ix_orders_has_photos", "orders", ["has_photos"])

    op.create_table(
        "order_photos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.BigInteger, sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", _enum("photo_kind_enum", "device", "serial", "defect", "other"), nullable=False),
        sa.Column("tg_file_id", sa.String(256), nullable=False),
        sa.Column("gallery_chat_id", sa.BigInteger),
        sa.Column("gallery_message_id", sa.BigInteger),
        sa.Column("uploaded_by", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_order_photos_order_id", "order_photos", ["order_id"])

    op.create_table(
        "queue_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.BigInteger, sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("updated_by_admin_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_queue_items_position", "queue_items", ["position"])

    op.create_table(
        "requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.BigInteger, sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", _enum("request_type_enum", "asbis", "it4", "custom"), nullable=False),
        sa.Column("payload_text", sa.Text),
        sa.Column("status", _enum("request_status_enum", "open", "in_progress", "done", "rejected"),
                  nullable=False, server_default="open"),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("assigned_to", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_requests_order_id", "requests", ["order_id"])
    op.create_index("ix_requests_status", "requests", ["status"])

    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("order_id", sa.BigInteger, sa.ForeignKey("orders.id", ondelete="SET NULL")),
        sa.Column("actor_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("payload", JSONType),
        sa.Column("gallery_message_id", sa.BigInteger),
        sa.Column("posted_to_feed", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_events_kind", "events", ["kind"])
    op.create_index("ix_events_posted_to_feed", "events", ["posted_to_feed"])

    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", JSONType),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("events")
    op.drop_table("requests")
    op.drop_table("queue_items")
    op.drop_table("order_photos")
    op.drop_table("orders")
    op.drop_table("users")
