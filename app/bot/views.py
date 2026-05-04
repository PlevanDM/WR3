"""DM widget views — single updating messages instead of chains of new posts.

Each renderer returns a (text, keyboard) pair. Handlers either send a new
message (first entry) or edit an existing one (navigation/actions). Callback
data uses short `d*` prefixes to stay within the 64-byte Telegram limit:

* `dq:*` — queue widget (engineer/admin)
* `dm:*` — "my orders" widget (engineer)
* `dr:*` — "my requests" widget (engineer)
* `di:*` — manager inbox widget
"""
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.session import session_scope
from app.db import repo
from app.db.models import Role, User, RequestStatus, RequestType
from app.bot.services.settings_cache import finished_status_ids, sla_days, fresh_days
from app.bot.services.ro_url import resolve_ro_url
from app.bot.services.deep_link import order_deep_link
from app.bot.texts import (
    REQUEST_LABELS, status_emoji, human_request_status, kind_badge, KIND_LABELS,
)


PAGE = 6          # items per page in list widgets
NOOP = "d:noop"


def _inbox_can_act(user: User) -> bool:
    """Manager/admin may confirm/reject/export; owner is read-only in inbox UI."""
    return user.role in (Role.manager, Role.admin)


def _short(s: str | None, n: int) -> str:
    if not s:
        return "—"
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _age_days(dt: datetime | None) -> int:
    """Days since `dt`, safe for naive datetimes (treated as UTC)."""
    if not dt:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


# ============ queue ============
async def render_queue_view(
    user: User, page: int = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    finished = await finished_status_ids()
    fdays = await fresh_days()
    async with session_scope() as s:
        total = await repo.count_queue(s, exclude_status_ids=finished, fresh_days=fdays)
        rows = await repo.queue_page(
            s, offset=page * PAGE, limit=PAGE + 1,
            exclude_status_ids=finished, fresh_days=fdays,
        )
    has_next = len(rows) > PAGE
    rows = rows[:PAGE]
    pages = max(1, (total + PAGE - 1) // PAGE)

    head = (
        f"📋 <b>Очередь</b> · заказов: <b>{total}</b> · "
        f"стр. {min(page + 1, pages)}/{pages}\n"
        f"<i>Нажми на заказ, чтобы открыть карточку.</i>"
    )
    if not rows:
        body = "\n\n<i>📭 Очередь пуста.</i>"
        return head + body, _simple_refresh_kb(page, has_prev=page > 0, has_next=False)

    lines = []
    b = InlineKeyboardBuilder()
    for q, o in rows:
        marker = "🟢" if o.assigned_engineer_id is None else "🟦"
        mine = (user.id == o.assigned_engineer_id)
        tag = " (моё)" if mine else ""
        dev = _short(o.device, 38)
        kb = kind_badge(o.is_paid)
        lines.append(
            f"{marker} <b>{q.position}.</b> {kb} <code>#{o.number}</code> · {dev}{tag}"
        )
        btn = f"{q.position}. #{o.number}"
        b.button(text=btn, callback_data=f"dq:o:{o.id}:{page}")
    # nav row
    row_btns = []
    if page > 0:
        row_btns.append(("◀️", f"dq:p:{page - 1}"))
    row_btns.append((f"{min(page + 1, pages)}/{pages}", NOOP))
    if has_next:
        row_btns.append(("▶️", f"dq:p:{page + 1}"))
    for t, cb in row_btns:
        b.button(text=t, callback_data=cb)
    b.button(text="🔄 Обновить", callback_data=f"dq:p:{page}")
    b.adjust(*([1] * len(rows) + [len(row_btns), 1]))
    text = head + "\n\n" + "\n".join(lines)
    return text, b.as_markup()


def _simple_refresh_kb(page: int, *, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if has_prev:
        b.button(text="◀️", callback_data=f"dq:p:{page - 1}")
    b.button(text="🔄", callback_data=f"dq:p:{page}")
    if has_next:
        b.button(text="▶️", callback_data=f"dq:p:{page + 1}")
    b.adjust(3)
    return b.as_markup()


async def render_queue_order_view(
    user: User, order_id: int, back_page: int, bot: Bot,
) -> tuple[str, InlineKeyboardMarkup] | None:
    async with session_scope() as s:
        o = await repo.get_order(s, order_id)
        if not o:
            return None
        n = await repo.photos_count(s, order_id)
        holder_name = None
        if o.assigned_engineer_id:
            from sqlalchemy import select
            from app.db.models import User as UM
            h = (await s.execute(
                select(UM).where(UM.id == o.assigned_engineer_id)
            )).scalar_one_or_none()
            if h:
                holder_name = h.full_name or (h.username and f"@{h.username}") or str(h.tg_id)

    taken_by_me = (o.assigned_engineer_id == user.id)
    lines = [
        f"📋 <b>Очередь → #{o.number}</b>  ·  {KIND_LABELS[bool(o.is_paid)]}",
        f"📷 <b>{n}</b>  ·  " + (f"{status_emoji(o.status_name)} {o.status_name}" if o.status_name else "—"),
    ]
    if o.device:      lines.append(f"📦 {o.device}")
    if o.serial:      lines.append(f"🔢 {o.serial}")
    if o.client_name: lines.append(f"👤 {o.client_name}")
    if holder_name:
        tag = " (ты)" if taken_by_me else ""
        lines.append(f"🛠 на: {holder_name}{tag}")
    if o.last_activity_at:
        lines.append(f"🕘 {o.last_activity_at:%d.%m %H:%M}")

    ro_url = await resolve_ro_url(o)
    dl = await order_deep_link(bot, order_id)
    is_admin = user.role == Role.admin
    can_take = user.role in (Role.engineer, Role.admin)
    can_request = user.role in (Role.engineer, Role.manager, Role.admin)

    b = InlineKeyboardBuilder()
    b.button(text="🔗 RemOnline", url=ro_url)
    b.button(text="💬 В боте",   url=dl)
    sizes = [2]
    if can_take:
        if taken_by_me:
            b.button(text="↩️ Вернуть в очередь", callback_data=f"dq:un:{order_id}:{back_page}")
        elif o.assigned_engineer_id is None:
            b.button(text="👤 Взять", callback_data=f"dq:tk:{order_id}:{back_page}")
        else:
            b.button(text="⛔ Занят", callback_data=NOOP)
        sizes.append(1)
    if can_request:
        b.button(text="📨 Запрос по заказу", callback_data=f"eng:reqmenu:{order_id}")
        sizes.append(1)
    if is_admin:
        b.button(text="⬆️ Выше",    callback_data=f"adm:q:up:{order_id}")
        b.button(text="⬇️ Ниже",    callback_data=f"adm:q:dn:{order_id}")
        b.button(text="⏫ В начало", callback_data=f"adm:q:top:{order_id}")
        sizes.append(3)
        if o.assigned_engineer_id is None:
            b.button(text="👷 Назначить", callback_data=f"adm:as:{order_id}:0")
        else:
            b.button(text="🔄 Передать другому", callback_data=f"adm:as:{order_id}:0")
            b.button(text="↩️ Снять с инженера", callback_data=f"adm:un:{order_id}")
        sizes.append(2 if o.assigned_engineer_id else 1)
    b.button(text="⬅️ К очереди", callback_data=f"dq:p:{back_page}")
    sizes.append(1)
    b.adjust(*sizes)
    return "\n".join(lines), b.as_markup()


# ============ my orders ============
async def render_mine_view(
    user: User, page: int = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    finished = await finished_status_ids()
    async with session_scope() as s:
        rows = await repo.orders_assigned_to(
            s, user.id, exclude_status_ids=finished, limit=100, fresh_days=None,
        )
    total = len(rows)
    pages = max(1, (total + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    window = rows[page * PAGE:(page + 1) * PAGE]
    head = (f"🧰 <b>Мои заказы</b> · в работе: <b>{total}</b> · "
            f"стр. {page + 1}/{pages}")
    if not window:
        return head + "\n\n<i>📭 Сейчас на тебе ничего нет.</i>", _mine_empty_kb()
    lines = []
    b = InlineKeyboardBuilder()
    for o in window:
        st = status_emoji(o.status_name)
        dev = _short(o.device, 38)
        kb = kind_badge(o.is_paid)
        lines.append(f"{st} {kb} <code>#{o.number}</code> · {dev}")
        b.button(text=f"#{o.number}", callback_data=f"dm:o:{o.id}:{page}")
    row = []
    if page > 0:      row.append(("◀️", f"dm:p:{page - 1}"))
    row.append((f"{page + 1}/{pages}", NOOP))
    if page < pages - 1: row.append(("▶️", f"dm:p:{page + 1}"))
    for t, cb in row:
        b.button(text=t, callback_data=cb)
    b.button(text="🔄 Обновить", callback_data=f"dm:p:{page}")
    b.adjust(*([1] * len(window) + [len(row), 1]))
    return head + "\n\n" + "\n".join(lines), b.as_markup()


def _mine_empty_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Обновить", callback_data="dm:p:0")
    return b.as_markup()


async def render_mine_order_view(
    user: User, order_id: int, back_page: int, bot: Bot,
) -> tuple[str, InlineKeyboardMarkup] | None:
    async with session_scope() as s:
        o = await repo.get_order(s, order_id)
        if not o:
            return None
        n = await repo.photos_count(s, order_id)
    lines = [
        f"🧰 <b>Мои → #{o.number}</b>  ·  {KIND_LABELS[bool(o.is_paid)]}",
        f"📷 <b>{n}</b>  ·  " + (f"{status_emoji(o.status_name)} {o.status_name}" if o.status_name else "—"),
    ]
    if o.device:   lines.append(f"📦 {o.device}")
    if o.serial:   lines.append(f"🔢 {o.serial}")
    if o.client_name: lines.append(f"👤 {o.client_name}")
    if o.last_activity_at:
        lines.append(f"🕘 {o.last_activity_at:%d.%m %H:%M}")

    ro_url = await resolve_ro_url(o)
    dl = await order_deep_link(bot, order_id)
    b = InlineKeyboardBuilder()
    b.button(text="🔗 RemOnline", url=ro_url)
    b.button(text="💬 В боте",   url=dl)
    b.button(text="↩️ Вернуть в очередь", callback_data=f"dq:un:{order_id}:-1")
    b.button(text="📨 Запрос по заказу",  callback_data=f"eng:reqmenu:{order_id}")
    sizes = [2, 1, 1]
    if user.role == Role.admin:
        b.button(text="🔄 Передать другому", callback_data=f"adm:as:{order_id}:0")
        sizes.append(1)
    b.button(text="⬅️ К моим",            callback_data=f"dm:p:{back_page}")
    sizes.append(1)
    b.adjust(*sizes)
    return "\n".join(lines), b.as_markup()


# ============ admin: assign picker ============
ASSIGN_PAGE = 6


async def render_assign_picker(
    user: User, order_id: int, page: int = 0,
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Picker of engineers for a manager/admin to (re)assign an order."""
    async with session_scope() as s:
        o = await repo.get_order(s, order_id)
        if not o:
            return None
        engineers = await repo.list_users(s, role=Role.engineer)
    engineers = [e for e in engineers if e.is_active]
    total = len(engineers)
    pages = max(1, (total + ASSIGN_PAGE - 1) // ASSIGN_PAGE)
    page = max(0, min(page, pages - 1))
    window = engineers[page * ASSIGN_PAGE:(page + 1) * ASSIGN_PAGE]

    head = (
        f"👷 <b>Назначить инженера</b> · #{o.number}\n"
        f"<i>Выбери исполнителя из активных инженеров.</i>"
    )
    if not engineers:
        b = InlineKeyboardBuilder()
        b.button(text="⬅️ Назад", callback_data=f"dq:o:{order_id}:0")
        return head + "\n\n<i>Нет активных инженеров.</i>", b.as_markup()

    b = InlineKeyboardBuilder()
    for e in window:
        nm = e.full_name or (e.username and f"@{e.username}") or str(e.tg_id)
        marker = " · ✅" if e.id == o.assigned_engineer_id else ""
        # 💰 = paid engineer; visually highlights the right pick for paid orders.
        prefix = "💰" if getattr(e, "is_paid_engineer", False) else "👤"
        b.button(text=f"{prefix} {nm}{marker}", callback_data=f"adm:asgo:{order_id}:{e.id}")
    sizes = [1] * len(window)
    if pages > 1:
        row = []
        if page > 0:           row.append(("◀️", f"adm:as:{order_id}:{page - 1}"))
        row.append((f"{page + 1}/{pages}", NOOP))
        if page < pages - 1:   row.append(("▶️", f"adm:as:{order_id}:{page + 1}"))
        for t, cb in row:
            b.button(text=t, callback_data=cb)
        sizes.append(len(row))
    b.button(text="⬅️ Отмена", callback_data=f"dq:o:{order_id}:0")
    sizes.append(1)
    b.adjust(*sizes)
    return head, b.as_markup()


# ============ my requests ============
async def render_myreq_view(
    user: User, flt: str = "open",
) -> tuple[str, InlineKeyboardMarkup]:
    async with session_scope() as s:
        rows = await repo.requests_by_user(s, user.id, limit=50)
    opens = [(r, o) for r, o in rows if r.status in (RequestStatus.open, RequestStatus.in_progress)]
    closed = [(r, o) for r, o in rows if r.status in (RequestStatus.done, RequestStatus.rejected)]
    if flt == "closed":
        show = closed[:12]
        head = (
            f"🗂 <b>Мои запросы</b> · закрытые\n"
            f"<i>открытых {len(opens)} · закрытых {len(closed)}</i>"
        )
    else:
        flt = "open"
        show = opens[:12]
        head = (
            f"🗂 <b>Мои запросы</b> · открытые\n"
            f"<i>открытых {len(opens)} · закрытых {len(closed)}</i>"
        )

    if not show:
        empty = (
            "\n\n<i>📭 Здесь пока ничего нет.</i>"
            if flt == "open"
            else "\n\n<i>📭 Ни одного закрытого запроса.</i>"
        )
        return head + empty, _myreq_filter_kb(flt, open_n=len(opens), closed_n=len(closed))

    lines = []
    b = InlineKeyboardBuilder()
    for r, o in show:
        label = REQUEST_LABELS.get(r.type.value, r.type.value)
        self_cancel = (r.status == RequestStatus.rejected and r.assigned_to == r.created_by)
        note = "cancelled_by_author" if self_cancel else ""
        human = human_request_status(r.type.value, r.status.value, note=note)
        lines.append(
            f"• <b>#{r.id}</b> — {label} · <code>#{o.number}</code>\n"
            f"  {_short(o.device, 36)}\n"
            f"  {human} · {r.created_at:%d.%m %H:%M}"
        )
        if flt == "open":
            b.button(text=f"❎ Отменить запрос #{r.id}",
                     callback_data=f"eng:reqown:cancel:{r.id}")
    # filter switch
    b.button(
        text=f"{'✅ ' if flt == 'open' else ''}📂 Открытые ({len(opens)})",
        callback_data="dr:f:open",
    )
    b.button(
        text=f"{'✅ ' if flt == 'closed' else ''}✔️ Закрытые ({len(closed)})",
        callback_data="dr:f:closed",
    )
    b.button(text="🔄", callback_data=f"dr:f:{flt}")
    sizes = ([1] * (len(show) if flt == "open" else 0)) + [2, 1]
    b.adjust(*sizes)
    return head + "\n\n" + "\n\n".join(lines), b.as_markup()


def _myreq_filter_kb(flt: str, *, open_n: int, closed_n: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text=f"{'✅ ' if flt == 'open' else ''}📂 Открытые ({open_n})",
        callback_data="dr:f:open",
    )
    b.button(
        text=f"{'✅ ' if flt == 'closed' else ''}✔️ Закрытые ({closed_n})",
        callback_data="dr:f:closed",
    )
    b.button(text="🔄", callback_data=f"dr:f:{flt}")
    b.adjust(2, 1)
    return b.as_markup()


# ============ reception lists ============
async def render_reception_nophoto_view(
    user: User, page: int = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    finished = await finished_status_ids()
    fdays = await fresh_days()
    async with session_scope() as s:
        orders = await repo.orders_without_photos(
            s, limit=100, exclude_status_ids=finished, fresh_days=fdays,
        )
    total = len(orders)
    pages = max(1, (total + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    window = orders[page * PAGE:(page + 1) * PAGE]
    head = (f"📷 <b>Заказы без фото</b> · {total} шт. · "
            f"стр. {page + 1}/{pages}\n"
            f"<i>за последние {fdays} дн.</i>")
    b = InlineKeyboardBuilder()
    sizes: list[int] = []
    if not window:
        body = "\n\n<i>✅ У всех свежих заказов есть фото.</i>"
    else:
        lines = []
        for o in window:
            dev = _short(o.device, 38)
            age_d = _age_days(o.last_activity_at)
            age = f" · {age_d}д" if age_d >= 1 else ""
            lines.append(f"⚪ <code>#{o.number}</code> · {dev}{age}")
            b.button(text=f"📷 #{o.number}", callback_data=f"rcp:photo:{o.id}")
            sizes.append(1)
        body = "\n\n" + "\n".join(lines)
    nav = []
    if page > 0: nav.append(("◀️", f"dp:np:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", NOOP))
    if page < pages - 1: nav.append(("▶️", f"dp:np:{page + 1}"))
    for t, cb in nav:
        b.button(text=t, callback_data=cb)
    sizes.append(len(nav))
    b.button(text="🔄 Обновить", callback_data=f"dp:np:{page}")
    sizes.append(1)
    b.adjust(*sizes)
    return head + body, b.as_markup()


async def render_reception_stale_view(
    user: User, page: int = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    finished = await finished_status_ids()
    days = await sla_days()
    fdays = await fresh_days()
    async with session_scope() as s:
        orders = await repo.orders_stale(
            s, days=days, limit=100, exclude_status_ids=finished, fresh_days=fdays,
        )
    total = len(orders)
    pages = max(1, (total + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    window = orders[page * PAGE:(page + 1) * PAGE]
    head = (f"⏰ <b>Застряли</b> · без движения &gt; {days} дн.\n"
            f"<i>{total} шт. · стр. {page + 1}/{pages}</i>")
    b = InlineKeyboardBuilder()
    sizes: list[int] = []
    if not window:
        body = "\n\n<i>✅ Все заказы двигаются.</i>"
    else:
        lines = []
        for o in window:
            dev = _short(o.device, 38)
            age_d = _age_days(o.last_activity_at)
            lines.append(f"🟠 <code>#{o.number}</code> · {dev} · <b>{age_d} д</b>")
            b.button(text=f"📷 #{o.number}", callback_data=f"rcp:photo:{o.id}")
            sizes.append(1)
        body = "\n\n" + "\n".join(lines)
    nav = []
    if page > 0: nav.append(("◀️", f"dp:st:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", NOOP))
    if page < pages - 1: nav.append(("▶️", f"dp:st:{page + 1}"))
    for t, cb in nav:
        b.button(text=t, callback_data=cb)
    sizes.append(len(nav))
    b.button(text="🔄 Обновить", callback_data=f"dp:st:{page}")
    sizes.append(1)
    b.adjust(*sizes)
    return head + body, b.as_markup()


# ============ manager inbox ============
FILTER_LABELS = {"all": "Все", "asbis": "Асбис", "it4": "ИТ4",
                 "outsource": "Аутсорс", "custom": "Другое"}


async def render_inbox_view(
    user: User, flt: str = "all", page: int = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    rtype = None
    if flt in ("asbis", "it4", "custom", "outsource"):
        rtype = RequestType(flt)
    async with session_scope() as s:
        rows = await repo.open_requests(s, rtype=rtype)
    total = len(rows)
    pages = max(1, (total + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    window = rows[page * PAGE:(page + 1) * PAGE]

    counts: dict[str, int] = {}
    async with session_scope() as s:
        all_rows = await repo.open_requests(s)
        for r, _ in all_rows:
            counts[r.type.value] = counts.get(r.type.value, 0) + 1
    total_open = sum(counts.values())
    summary_parts = [f"{REQUEST_LABELS.get(k, k)} {v}" for k, v in counts.items() if v]
    summary = " · ".join(summary_parts) if summary_parts else "пусто"

    head = (
        f"📨 <b>Входящие запросы</b> · {FILTER_LABELS.get(flt, flt).lower()}\n"
        f"<i>в этом фильтре: {total} · всего открытых: {total_open} · "
        f"стр. {page + 1}/{pages}</i>\n"
        f"{summary}"
    )

    b = InlineKeyboardBuilder()
    sizes: list[int] = []

    if not window:
        body = "\n\n<i>✅ Здесь всё разобрано.</i>"
    else:
        lines = []
        for r, o in window:
            label = REQUEST_LABELS.get(r.type.value, r.type.value)
            human = human_request_status(r.type.value, r.status.value)
            snippet = (r.payload_text or "").splitlines()[0][:80] if r.payload_text else ""
            lines.append(
                f"• <b>#{r.id}</b> — {label} · <code>#{o.number}</code>\n"
                f"  {human} · {r.created_at:%d.%m %H:%M}"
                + (f"\n  <i>{snippet}</i>" if snippet else "")
            )
            b.button(text=f"Открыть #{r.id} · #{o.number}",
                     callback_data=f"di:o:{r.id}:{flt}:{page}")
            sizes.append(1)
        body = "\n\n" + "\n\n".join(lines)

    # filter row
    for code, label in (("all", "Все"), ("asbis", "Асбис"), ("it4", "ИТ4"),
                         ("outsource", "Аутсорс"), ("custom", "Другое")):
        mark = "✅ " if flt == code else ""
        c = counts.get(code, 0) if code != "all" else sum(counts.values())
        b.button(text=f"{mark}{label} ({c})", callback_data=f"di:f:{code}:0")
    sizes.append(5)

    nav = []
    if page > 0: nav.append(("◀️", f"di:f:{flt}:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", NOOP))
    if page < pages - 1: nav.append(("▶️", f"di:f:{flt}:{page + 1}"))
    for t, cb in nav:
        b.button(text=t, callback_data=cb)
    sizes.append(len(nav))
    # export only for narrow type-filters (Asbis/IT4 flavor); not for read-only owner
    if flt in ("asbis", "it4") and total and _inbox_can_act(user):
        b.button(text="📤 Выгрузить списком", callback_data=f"mgr:list:export:{flt}")
        sizes.append(1)
    b.button(text="🔄 Обновить", callback_data=f"di:f:{flt}:{page}")
    sizes.append(1)

    b.adjust(*sizes)
    return head + body, b.as_markup()


async def render_inbox_request_view(
    user: User, rid: int, flt: str, page: int,
) -> tuple[str, InlineKeyboardMarkup] | None:
    from sqlalchemy import select
    from app.db.models import User as UM

    async with session_scope() as s:
        r = await repo.get_request(s, rid)
        if not r:
            return None
        o = await repo.get_order(s, r.order_id)
        author = "—"
        if r.created_by:
            u = (await s.execute(select(UM).where(UM.id == r.created_by))).scalar_one_or_none()
            if u:
                role_label = {
                    "engineer": "🛠 инженер", "manager": "📋 менеджер",
                    "reception": "🛎 приёмка", "admin": "🛡 админ",
                    "owner": "👑 владелец",
                }.get(u.role.value, u.role.value)
                nm = u.full_name or (u.username and f"@{u.username}") or str(u.tg_id)
                author = f"{nm} · {role_label}"

    type_label = REQUEST_LABELS.get(r.type.value, r.type.value)
    sn_line = f"\n🔢 {o.serial}" if (o and o.serial) else ""
    human = human_request_status(r.type.value, r.status.value)
    text = (
        f"📨 <b>Запрос #{r.id}</b> · {type_label}\n"
        f"📦 <b>#{o.number if o else '—'}</b> — {o.device if (o and o.device) else '—'}{sn_line}\n"
        f"От: {author}\n"
        f"Статус: {human}\n"
        f"Создан: {r.created_at:%d.%m %H:%M}\n\n"
        f"<i>{r.payload_text or '—'}</i>"
    )

    b = InlineKeyboardBuilder()
    is_terminal = r.status in (RequestStatus.done, RequestStatus.rejected)
    can_act = _inbox_can_act(user)
    if not is_terminal and can_act:
        if r.type in (RequestType.asbis, RequestType.it4):
            b.button(text="✅ Подтвердить",       callback_data=f"mgr:req:done:{r.id}")
            b.button(text="🚫 Отклонить с комм.", callback_data=f"mgr:req:rejc:{r.id}")
            sizes: list[int] = [2]
        else:
            b.button(text="⚙️ В работу", callback_data=f"mgr:req:ip:{r.id}")
            b.button(text="✅ Готово",    callback_data=f"mgr:req:done:{r.id}")
            b.button(text="🚫 Отклонить с комм.", callback_data=f"mgr:req:rejc:{r.id}")
            sizes = [2, 1]
    else:
        sizes = []
    b.button(text="⬅️ Входящие", callback_data=f"di:f:{flt}:{page}")
    sizes.append(1)
    b.adjust(*sizes)
    return text, b.as_markup()
