from __future__ import annotations
import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, Enum as SAEnum,
    JSON, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    admin = "admin"
    reception = "reception"
    engineer = "engineer"
    manager = "manager"


class PhotoKind(str, enum.Enum):
    device = "device"
    serial = "serial"
    defect = "defect"
    other = "other"


class RequestType(str, enum.Enum):
    asbis = "asbis"
    it4 = "it4"
    custom = "custom"
    outsource = "outsource"  # paid repair forwarded to external service


class RequestStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    done = "done"
    rejected = "rejected"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="role_enum"))
    ro_employee_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_paid_engineer: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # ro order id
    number: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    status_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    status_name: Mapped[Optional[str]] = mapped_column(String(128))
    client_name: Mapped[Optional[str]] = mapped_column(String(255))
    device: Mapped[Optional[str]] = mapped_column(String(255))
    serial: Mapped[Optional[str]] = mapped_column(String(255))
    defect: Mapped[Optional[str]] = mapped_column(Text)
    created_at_ro: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    assigned_engineer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    has_photos: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    public_url: Mapped[Optional[str]] = mapped_column(String(512))
    raw: Mapped[Optional[dict]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OrderPhoto(Base):
    __tablename__ = "order_photos"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    kind: Mapped[PhotoKind] = mapped_column(SAEnum(PhotoKind, name="photo_kind_enum"))
    media_type: Mapped[str] = mapped_column(String(16), default="photo")
    tg_file_id: Mapped[str] = mapped_column(String(256))
    gallery_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    gallery_message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    uploaded_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QueueItem(Base):
    __tablename__ = "queue_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    position: Mapped[int] = mapped_column(Integer, index=True)
    updated_by_admin_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Request(Base):
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    type: Mapped[RequestType] = mapped_column(SAEnum(RequestType, name="request_type_enum"))
    payload_text: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[RequestStatus] = mapped_column(SAEnum(RequestStatus, name="request_status_enum"), default=RequestStatus.open, index=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    assigned_to: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    payload: Mapped[Optional[dict]] = mapped_column(JSONType)
    gallery_message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    posted_to_feed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Optional[dict]] = mapped_column(JSONType)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
