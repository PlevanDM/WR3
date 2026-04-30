from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import select, update, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    User, Role, Order, OrderPhoto, PhotoKind, QueueItem,
    Request, RequestType, RequestStatus, Event, Setting,
)


def _fresh_cutoff(days: int | None) -> datetime | None:
    if not days or days <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def _fresh_cond(cutoff: datetime | None):
    if cutoff is None:
        return None
    return or_(Order.last_activity_at >= cutoff, Order.created_at_ro >= cutoff)


# ---------- users ----------
async def get_user_by_tg(s: AsyncSession, tg_id: int) -> Optional[User]:
    return (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


async def list_users(s: AsyncSession, role: Optional[Role] = None) -> Sequence[User]:
    q = select(User).order_by(User.id)
    if role is not None:
        q = q.where(User.role == role)
    return (await s.execute(q)).scalars().all()


async def upsert_user(s: AsyncSession, tg_id: int, role: Role, *, username: str | None = None,
                      full_name: str | None = None, ro_employee_id: int | None = None) -> User:
    u = await get_user_by_tg(s, tg_id)
    if u:
        u.role = role
        if username is not None: u.username = username
        if full_name is not None: u.full_name = full_name
        if ro_employee_id is not None: u.ro_employee_id = ro_employee_id
        u.is_active = True
    else:
        u = User(tg_id=tg_id, role=role, username=username, full_name=full_name, ro_employee_id=ro_employee_id)
        s.add(u)
        await s.flush()
    return u


async def set_user_active(s: AsyncSession, tg_id: int, active: bool) -> None:
    await s.execute(update(User).where(User.tg_id == tg_id).values(is_active=active))


async def release_orders_of_user(s: AsyncSession, user_id: int) -> list[int]:
    """Detach all orders from a user and put them back on the queue. Returns detached order ids."""
    rows = (await s.execute(
        select(Order.id).where(Order.assigned_engineer_id == user_id)
    )).scalars().all()
    if not rows:
        return []
    await s.execute(
        update(Order).where(Order.assigned_engineer_id == user_id).values(assigned_engineer_id=None)
    )
    for oid in rows:
        await ensure_queue_item(s, oid)
    return list(rows)


# ---------- orders ----------
async def upsert_order(s: AsyncSession, data: dict) -> tuple[Order, bool]:
    existing = (await s.execute(select(Order).where(Order.id == data["id"]))).scalar_one_or_none()
    if existing is None:
        o = Order(**data)
        s.add(o)
        await s.flush()
        return o, True
    for k, v in data.items():
        setattr(existing, k, v)
    return existing, False


async def get_order(s: AsyncSession, order_id: int) -> Optional[Order]:
    return (await s.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()


async def orders_without_photos(s: AsyncSession, limit: int = 50,
                                 exclude_status_ids: Sequence[int] | None = None,
                                 fresh_days: int | None = None) -> Sequence[Order]:
    q = select(Order).where(Order.has_photos == False)  # noqa: E712
    if exclude_status_ids:
        q = q.where(~Order.status_id.in_(list(exclude_status_ids)))
    cond = _fresh_cond(_fresh_cutoff(fresh_days))
    if cond is not None:
        q = q.where(cond)
    q = q.order_by(Order.created_at_ro.desc()).limit(limit)
    return (await s.execute(q)).scalars().all()


async def orders_stale(s: AsyncSession, days: int, limit: int = 50,
                       exclude_status_ids: Sequence[int] | None = None,
                       fresh_days: int | None = None) -> Sequence[Order]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    q = select(Order).where(Order.last_activity_at < cutoff)
    if exclude_status_ids:
        q = q.where(~Order.status_id.in_(list(exclude_status_ids)))
    fcond = _fresh_cond(_fresh_cutoff(fresh_days))
    if fcond is not None:
        q = q.where(fcond)
    q = q.order_by(Order.last_activity_at.asc()).limit(limit)
    return (await s.execute(q)).scalars().all()


# ---------- photos ----------
async def add_photo(s: AsyncSession, order_id: int, kind: PhotoKind, tg_file_id: str,
                    *, chat_id: int | None, message_id: int | None, uploaded_by: int | None,
                    media_type: str = "photo") -> OrderPhoto:
    p = OrderPhoto(order_id=order_id, kind=kind, tg_file_id=tg_file_id,
                   gallery_chat_id=chat_id, gallery_message_id=message_id,
                   uploaded_by=uploaded_by, media_type=media_type)
    s.add(p)
    await s.execute(update(Order).where(Order.id == order_id).values(has_photos=True))
    await s.flush()
    return p


async def photos_for_order(s: AsyncSession, order_id: int) -> Sequence[OrderPhoto]:
    return (await s.execute(select(OrderPhoto).where(OrderPhoto.order_id == order_id).order_by(OrderPhoto.id))).scalars().all()


async def photos_count(s: AsyncSession, order_id: int) -> int:
    return int((await s.execute(
        select(func.count(OrderPhoto.id)).where(OrderPhoto.order_id == order_id)
    )).scalar_one() or 0)


async def photo_kinds_count(s: AsyncSession, order_id: int) -> int:  # legacy alias
    return await photos_count(s, order_id)


async def get_order_by_number(s: AsyncSession, number: str) -> Optional[Order]:
    return (await s.execute(select(Order).where(Order.number == number))).scalar_one_or_none()


async def reindex_queue(s: AsyncSession) -> int:
    items = (await s.execute(select(QueueItem).order_by(QueueItem.position.asc(), QueueItem.id.asc()))).scalars().all()
    for i, it in enumerate(items, start=1):
        if it.position != i:
            it.position = i
    return len(items)


async def seed_queue_from_orders(s: AsyncSession, exclude_status_ids: Sequence[int] | None = None,
                                 fresh_days: int | None = None) -> int:
    """Populate queue from all non-finished orders that aren't yet in the queue."""
    q = select(Order.id).outerjoin(QueueItem, QueueItem.order_id == Order.id).where(QueueItem.order_id.is_(None))
    if exclude_status_ids:
        q = q.where(~Order.status_id.in_(list(exclude_status_ids)))
    fcond = _fresh_cond(_fresh_cutoff(fresh_days))
    if fcond is not None:
        q = q.where(fcond)
    ids = [row[0] for row in (await s.execute(q)).all()]
    added = 0
    for oid in ids:
        await ensure_queue_item(s, oid)
        added += 1
    return added


# ---------- queue ----------
async def ensure_queue_item(s: AsyncSession, order_id: int) -> QueueItem:
    existing = (await s.execute(select(QueueItem).where(QueueItem.order_id == order_id))).scalar_one_or_none()
    if existing:
        return existing
    max_pos = (await s.execute(select(func.coalesce(func.max(QueueItem.position), 0)))).scalar_one()
    q = QueueItem(order_id=order_id, position=int(max_pos) + 1)
    s.add(q)
    await s.flush()
    return q


async def queue_page(s: AsyncSession, offset: int = 0, limit: int = 10,
                     exclude_status_ids: Sequence[int] | None = None,
                     fresh_days: int | None = None) -> Sequence[tuple[QueueItem, Order]]:
    q = (select(QueueItem, Order).join(Order, Order.id == QueueItem.order_id))
    if exclude_status_ids:
        q = q.where(~Order.status_id.in_(list(exclude_status_ids)))
    fcond = _fresh_cond(_fresh_cutoff(fresh_days))
    if fcond is not None:
        q = q.where(fcond)
    q = q.order_by(QueueItem.position.asc()).offset(offset).limit(limit)
    return (await s.execute(q)).all()


async def remove_queue_item(s: AsyncSession, order_id: int) -> None:
    from sqlalchemy import delete
    await s.execute(delete(QueueItem).where(QueueItem.order_id == order_id))


async def count_queue(s: AsyncSession, exclude_status_ids: Sequence[int] | None = None,
                      fresh_days: int | None = None) -> int:
    q = select(func.count(QueueItem.order_id)).join(Order, Order.id == QueueItem.order_id)
    if exclude_status_ids:
        q = q.where(~Order.status_id.in_(list(exclude_status_ids)))
    fcond = _fresh_cond(_fresh_cutoff(fresh_days))
    if fcond is not None:
        q = q.where(fcond)
    return int((await s.execute(q)).scalar_one())


async def move_queue(s: AsyncSession, order_id: int, direction: int) -> None:
    item = (await s.execute(select(QueueItem).where(QueueItem.order_id == order_id))).scalar_one_or_none()
    if not item:
        return
    if direction < 0:
        neigh = (await s.execute(
            select(QueueItem).where(QueueItem.position < item.position).order_by(QueueItem.position.desc()).limit(1)
        )).scalar_one_or_none()
    else:
        neigh = (await s.execute(
            select(QueueItem).where(QueueItem.position > item.position).order_by(QueueItem.position.asc()).limit(1)
        )).scalar_one_or_none()
    if not neigh:
        return
    item.position, neigh.position = neigh.position, item.position
    item.updated_by_admin_at = datetime.now(timezone.utc)


async def move_queue_top(s: AsyncSession, order_id: int) -> None:
    min_pos = (await s.execute(select(func.coalesce(func.min(QueueItem.position), 1)))).scalar_one()
    item = (await s.execute(select(QueueItem).where(QueueItem.order_id == order_id))).scalar_one_or_none()
    if not item:
        return
    item.position = int(min_pos) - 1
    item.updated_by_admin_at = datetime.now(timezone.utc)


# ---------- requests ----------
async def create_request(s: AsyncSession, order_id: int, rtype: RequestType, payload: str,
                         created_by: int | None) -> Request:
    r = Request(order_id=order_id, type=rtype, payload_text=payload, created_by=created_by)
    s.add(r)
    await s.flush()
    return r


async def open_requests(s: AsyncSession,
                         *, rtype: RequestType | None = None,
                         include_in_progress: bool = False,
                         ) -> Sequence[tuple[Request, Order]]:
    statuses = [RequestStatus.open]
    if include_in_progress:
        statuses.append(RequestStatus.in_progress)
    q = (select(Request, Order)
         .join(Order, Order.id == Request.order_id)
         .where(Request.status.in_(statuses)))
    if rtype is not None:
        q = q.where(Request.type == rtype)
    q = q.order_by(Request.created_at.asc())
    return (await s.execute(q)).all()


async def requests_by_user(
    s: AsyncSession, user_id: int,
    *, statuses: Sequence[RequestStatus] | None = None, limit: int = 30,
) -> Sequence[tuple[Request, Order]]:
    q = (select(Request, Order)
         .join(Order, Order.id == Request.order_id)
         .where(Request.created_by == user_id))
    if statuses:
        q = q.where(Request.status.in_(list(statuses)))
    q = q.order_by(Request.created_at.desc()).limit(limit)
    return (await s.execute(q)).all()


async def get_request(s: AsyncSession, request_id: int) -> Optional[Request]:
    return (await s.execute(select(Request).where(Request.id == request_id))).scalar_one_or_none()


async def claim_order(s: AsyncSession, order_id: int, user_id: int) -> str:
    """Atomically claim a free order for a user.

    Returns one of:
      'ok'           — just claimed by this user;
      'already_mine' — already owned by this user before;
      'held'         — currently owned by somebody else;
      'not_found'    — order doesn't exist.

    Uses a conditional UPDATE (WHERE assigned_engineer_id IS NULL) so parallel
    take attempts can't both win.
    """
    order = (await s.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    if not order:
        return "not_found"
    if order.assigned_engineer_id == user_id:
        return "already_mine"
    if order.assigned_engineer_id is not None:
        return "held"
    res = await s.execute(
        update(Order)
        .where(Order.id == order_id, Order.assigned_engineer_id.is_(None))
        .values(assigned_engineer_id=user_id)
    )
    if (res.rowcount or 0) == 0:
        # lost the race between our SELECT and UPDATE — re-check
        await s.refresh(order)
        if order.assigned_engineer_id == user_id:
            return "already_mine"
        return "held"
    await remove_queue_item(s, order_id)
    return "ok"


async def release_order(s: AsyncSession, order_id: int, user_id: int) -> str:
    """Atomically release an order back to the queue if it is currently this user's.

    Returns: 'ok' | 'not_yours' | 'not_found'.
    """
    order = (await s.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    if not order:
        return "not_found"
    if order.assigned_engineer_id != user_id:
        return "not_yours"
    res = await s.execute(
        update(Order)
        .where(Order.id == order_id, Order.assigned_engineer_id == user_id)
        .values(assigned_engineer_id=None)
    )
    if (res.rowcount or 0) == 0:
        return "not_yours"
    await ensure_queue_item(s, order_id)
    return "ok"


async def set_user_paid_engineer(s: AsyncSession, tg_id: int, value: bool) -> User | None:
    u = await get_user_by_tg(s, tg_id)
    if not u:
        return None
    u.is_paid_engineer = bool(value)
    return u


async def list_paid_engineers(s: AsyncSession) -> Sequence[User]:
    q = (select(User)
         .where(User.role == Role.engineer,
                User.is_active.is_(True),
                User.is_paid_engineer.is_(True))
         .order_by(User.id))
    return (await s.execute(q)).scalars().all()


async def force_assign_order(s: AsyncSession, order_id: int, engineer_id: int | None) -> tuple[str, int | None]:
    """Manager/admin override: assign order to engineer (or None to unassign).

    Returns (status, prev_engineer_id). Status: 'ok' | 'noop' | 'not_found'.
    Removes the order from the public queue when assigning, and puts it back
    when unassigning. Bypasses the engineer-only «who took first» rule.
    """
    order = (await s.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    if not order:
        return "not_found", None
    prev = order.assigned_engineer_id
    if prev == engineer_id:
        return "noop", prev
    await s.execute(
        update(Order).where(Order.id == order_id).values(assigned_engineer_id=engineer_id)
    )
    if engineer_id is None:
        await ensure_queue_item(s, order_id)
    else:
        await remove_queue_item(s, order_id)
    return "ok", prev


async def orders_assigned_to(s: AsyncSession, user_id: int,
                              exclude_status_ids: Sequence[int] | None = None,
                              limit: int = 30,
                              fresh_days: int | None = None) -> Sequence[Order]:
    q = select(Order).where(Order.assigned_engineer_id == user_id)
    if exclude_status_ids:
        q = q.where(~Order.status_id.in_(list(exclude_status_ids)))
    fcond = _fresh_cond(_fresh_cutoff(fresh_days))
    if fcond is not None:
        q = q.where(fcond)
    q = q.order_by(Order.last_activity_at.desc().nullslast()).limit(limit)
    return (await s.execute(q)).scalars().all()


async def set_request_status(s: AsyncSession, request_id: int, status: RequestStatus,
                             assigned_to: int | None = None) -> Optional[Request]:
    r = (await s.execute(select(Request).where(Request.id == request_id))).scalar_one_or_none()
    if not r:
        return None
    r.status = status
    if assigned_to is not None:
        r.assigned_to = assigned_to
    if status in (RequestStatus.done, RequestStatus.rejected):
        r.closed_at = datetime.now(timezone.utc)
    return r


# ---------- events ----------
async def add_event(s: AsyncSession, kind: str, *, order_id: int | None = None,
                    actor_id: int | None = None, payload: dict | None = None) -> Event:
    e = Event(kind=kind, order_id=order_id, actor_id=actor_id, payload=payload or {})
    s.add(e)
    await s.flush()
    return e


async def pending_events(s: AsyncSession, limit: int = 50) -> Sequence[Event]:
    q = select(Event).where(Event.posted_to_feed == False).order_by(Event.id.asc()).limit(limit)  # noqa: E712
    return (await s.execute(q)).scalars().all()


async def mark_event_posted(s: AsyncSession, event_id: int, message_id: int | None) -> None:
    await s.execute(update(Event).where(Event.id == event_id).values(posted_to_feed=True, gallery_message_id=message_id))


# ---------- gallery cleanup ----------
async def gallery_tracked_message_ids(s: AsyncSession) -> list[int]:
    ev_ids = (await s.execute(
        select(Event.gallery_message_id).where(Event.gallery_message_id.is_not(None), Event.gallery_message_id > 0)
    )).scalars().all()
    ph_ids = (await s.execute(
        select(OrderPhoto.gallery_message_id).where(OrderPhoto.gallery_message_id.is_not(None))
    )).scalars().all()
    # widgets & order cards live in Setting (see app.bot.services.widgets)
    widget_row = (await s.execute(
        select(Setting.value).where(Setting.key == "widget_mids")
    )).scalar_one_or_none() or {}
    cards_row = (await s.execute(
        select(Setting.value).where(Setting.key == "order_card_mids")
    )).scalar_one_or_none() or {}
    widget_ids = [int(v) for v in (widget_row.values() if isinstance(widget_row, dict) else []) if v]
    card_ids = [int(v) for v in (cards_row.values() if isinstance(cards_row, dict) else []) if v]
    seen: set[int] = set()
    out: list[int] = []
    for mid in list(ev_ids) + list(ph_ids) + widget_ids + card_ids:
        if mid and mid not in seen:
            seen.add(mid); out.append(int(mid))
    return out


async def gallery_counts(s: AsyncSession) -> dict:
    ev = int((await s.execute(
        select(func.count()).where(Event.gallery_message_id.is_not(None), Event.gallery_message_id > 0)
    )).scalar_one())
    ph = int((await s.execute(
        select(func.count()).where(OrderPhoto.gallery_message_id.is_not(None))
    )).scalar_one())
    return {"events": ev, "photos": ph, "total": ev + ph}


async def clear_gallery_tracking(s: AsyncSession, message_ids: Sequence[int] | None = None) -> None:
    """Nullify gallery_message_id for given ids (or all if None)."""
    if message_ids is None:
        await s.execute(update(Event).values(gallery_message_id=None).where(Event.gallery_message_id.is_not(None)))
        await s.execute(update(OrderPhoto).values(gallery_message_id=None).where(OrderPhoto.gallery_message_id.is_not(None)))
        return
    if not message_ids:
        return
    ids = list(message_ids)
    await s.execute(update(Event).values(gallery_message_id=None)
                    .where(Event.gallery_message_id.in_(ids)))
    await s.execute(update(OrderPhoto).values(gallery_message_id=None)
                    .where(OrderPhoto.gallery_message_id.in_(ids)))
    # prune widget & card maps so they get re-created on next refresh
    id_set = {int(x) for x in ids}
    for key in ("widget_mids", "order_card_mids"):
        row = (await s.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
        if not row or not isinstance(row.value, dict):
            continue
        pruned = {k: v for k, v in row.value.items() if int(v) not in id_set}
        if pruned != row.value:
            row.value = pruned


# ---------- settings ----------
async def get_setting(s: AsyncSession, key: str) -> Optional[dict]:
    r = (await s.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    return r.value if r else None


async def set_setting(s: AsyncSession, key: str, value: dict) -> None:
    r = (await s.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    if r:
        r.value = value
    else:
        s.add(Setting(key=key, value=value))


async def get_role_override(s: AsyncSession, tg_id: int) -> Role | None:
    row = await get_setting(s, f"role_override:{tg_id}")
    if not isinstance(row, dict):
        return None
    raw = row.get("role")
    if not raw:
        return None
    try:
        return Role(str(raw))
    except Exception:
        return None


async def set_role_override(s: AsyncSession, tg_id: int, role: Role | None) -> None:
    key = f"role_override:{tg_id}"
    if role is None:
        r = (await s.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
        if r:
            await s.delete(r)
        return
    await set_setting(s, key, {"role": role.value})


# ---------- stats ----------
async def stats_orders(s: AsyncSession, *, fresh_days: int | None,
                       exclude_status_ids: Sequence[int] | None,
                       stale_days: int) -> dict:
    cutoff = _fresh_cutoff(fresh_days)
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    base = select(Order)
    if exclude_status_ids:
        base = base.where(~Order.status_id.in_(list(exclude_status_ids)))
    fcond = _fresh_cond(cutoff)
    if fcond is not None:
        base = base.where(fcond)
    total = int((await s.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    no_photos = int((await s.execute(
        select(func.count()).select_from(base.where(Order.has_photos == False).subquery())  # noqa: E712
    )).scalar_one())
    stale = int((await s.execute(
        select(func.count()).select_from(base.where(Order.last_activity_at < stale_cutoff).subquery())
    )).scalar_one())
    # today + week (by created_at_ro)
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=7)
    today_n = int((await s.execute(
        select(func.count()).select_from(base.where(Order.created_at_ro >= today).subquery())
    )).scalar_one())
    week_n = int((await s.execute(
        select(func.count()).select_from(base.where(Order.created_at_ro >= week).subquery())
    )).scalar_one())
    return {"total": total, "no_photos": no_photos, "stale": stale,
            "today": today_n, "week": week_n}


async def stats_requests(s: AsyncSession, *, fresh_days: int | None) -> dict:
    cutoff = _fresh_cutoff(fresh_days)
    out = {}
    for st in RequestStatus:
        q = select(func.count(Request.id)).where(Request.status == st)
        if cutoff is not None:
            q = q.where(Request.created_at >= cutoff)
        out[st.value] = int((await s.execute(q)).scalar_one())
    # by type (open + in_progress)
    by_type: dict[str, int] = {}
    for rt in RequestType:
        q = select(func.count(Request.id)).where(
            Request.type == rt,
            Request.status.in_([RequestStatus.open, RequestStatus.in_progress]),
        )
        if cutoff is not None:
            q = q.where(Request.created_at >= cutoff)
        by_type[rt.value] = int((await s.execute(q)).scalar_one())
    out["by_type"] = by_type
    return out


async def stats_engineer(s: AsyncSession, user_id: int, *, fresh_days: int | None,
                         exclude_status_ids: Sequence[int] | None) -> dict:
    cutoff = _fresh_cutoff(fresh_days)
    q = select(func.count(Order.id)).where(Order.assigned_engineer_id == user_id)
    if exclude_status_ids:
        q = q.where(~Order.status_id.in_(list(exclude_status_ids)))
    if cutoff is not None:
        q = q.where(_fresh_cond(cutoff))
    mine = int((await s.execute(q)).scalar_one())
    req_open = int((await s.execute(
        select(func.count(Request.id)).where(Request.created_by == user_id, Request.status == RequestStatus.open)
    )).scalar_one())
    req_done = int((await s.execute(
        select(func.count(Request.id)).where(Request.created_by == user_id, Request.status == RequestStatus.done)
    )).scalar_one())
    return {"mine": mine, "req_open": req_open, "req_done": req_done}
