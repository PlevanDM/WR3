from __future__ import annotations
from datetime import datetime, timezone

from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.db.session import session_scope
from app.db import repo
from app.db.models import Role, User, RequestType, RequestStatus
from app.bot.keyboards import (
    request_card_kb, request_type_kb, request_type_paid_kb, order_pick_kb,
    my_request_kb, eng_custom_text_kb,
)
from app.bot.services.sync_ro import push_request_comment, push_request_status_comment
from app.bot.services.settings_cache import finished_status_ids, fresh_days
from app.bot.texts import (
    REQ_PROMPT, REQUEST_LABELS, status_emoji, human_request_status,
)
from app.bot import views

router = Router(name="engineer")

PAGE_SIZE = 8


class RequestFSM(StatesGroup):
    waiting_text = State()
    waiting_order_no = State()


def _allowed(user: User | None) -> bool:
    return bool(user and user.is_active and user.role in (Role.engineer, Role.manager, Role.admin))


async def _badges(o, days: int) -> str:
    bs = []
    async with session_scope() as s:
        n = await repo.photos_count(s, o.id)
    bs.append("📷 —" if n == 0 else f"📷 {n}")
    if o.last_activity_at:
        la = o.last_activity_at
        if la.tzinfo is None:
            la = la.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - la).days
        if age >= days: bs.append(f"⏰ {age}д")
    return " · ".join(bs)


async def _card(q_or_none, o, days: int) -> str:
    pos = f"<b>#{q_or_none.position}</b>. " if q_or_none else ""
    head = f"{pos}Заказ <b>#{o.number}</b>"
    lines = [head]
    badge = await _badges(o, days)
    if badge: lines.append(badge)
    if o.device: lines.append(f"📦 {o.device}")
    if o.status_name: lines.append(f"{status_emoji(o.status_name)} {o.status_name}")
    if o.client_name: lines.append(f"👤 {o.client_name}")
    if o.last_activity_at: lines.append(f"🕘 {o.last_activity_at:%Y-%m-%d %H:%M}")
    return "\n".join(lines)


@router.message(Command("queue"))
@router.message(StateFilter(None), F.text.in_({"📋 Очередь", "Очередь"}))
async def show_queue(msg: Message, user: User | None, bot: Bot) -> None:
    if not _allowed(user): return
    try: await bot.send_chat_action(msg.chat.id, "typing")
    except Exception: pass
    text, kb = await views.render_queue_view(user, page=0)
    await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("dq:p:"))
async def cb_queue_page(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _allowed(user):
        await cb.answer("🔒 Нет доступа", show_alert=True); return
    page = int(cb.data.split(":")[2])
    text, kb = await views.render_queue_view(user, page=page)
    try: await cb.message.edit_text(text, reply_markup=kb)
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data.startswith("dq:o:"))
async def cb_queue_order(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _allowed(user):
        await cb.answer("🔒", show_alert=True); return
    _, _, oid_s, page_s = cb.data.split(":")
    out = await views.render_queue_order_view(user, int(oid_s), int(page_s), bot)
    if not out:
        await cb.answer("Заказ не найден", show_alert=True); return
    text, kb = out
    try: await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data.startswith("dq:tk:"))
async def cb_queue_take(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _allowed(user):
        await cb.answer("🔒", show_alert=True); return
    _, _, oid_s, page_s = cb.data.split(":")
    oid = int(oid_s)
    async with session_scope() as s:
        result = await repo.claim_order(s, oid, user.id)
        if result == "ok":
            order = await repo.get_order(s, oid)
            await repo.add_event(s, "order_taken", order_id=oid, payload={
                "number": order.number if order else oid,
                "engineer": user.full_name or str(user.tg_id),
            })
    if result == "ok":
        from app.bot.services.sync_ro import push_action_comment
        actor = user.full_name or (user.username and f"@{user.username}") or str(user.tg_id)
        await push_action_comment(oid, "Инженер взял в работу", actor=f"{actor} · 🛠 инженер")
        await cb.answer("👤 Взял в работу")
    elif result == "already_mine":
        await cb.answer("Уже у тебя")
    elif result == "held":
        await cb.answer("⛔ Уже занят", show_alert=True)
    else:
        await cb.answer("Заказ не найден", show_alert=True); return
    out = await views.render_queue_order_view(user, oid, int(page_s), bot)
    if out:
        text, kb = out
        try: await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except Exception: pass


@router.callback_query(F.data.startswith("dq:un:"))
async def cb_queue_untake(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _allowed(user):
        await cb.answer("🔒", show_alert=True); return
    _, _, oid_s, page_s = cb.data.split(":")
    oid = int(oid_s)
    async with session_scope() as s:
        result = await repo.release_order(s, oid, user.id)
        if result == "ok":
            order = await repo.get_order(s, oid)
            await repo.add_event(s, "order_untaken", order_id=oid, payload={
                "number": order.number if order else oid,
                "engineer": user.full_name or str(user.tg_id),
            })
    if result == "ok":
        from app.bot.services.sync_ro import push_action_comment
        actor = user.full_name or (user.username and f"@{user.username}") or str(user.tg_id)
        await push_action_comment(oid, "Инженер вернул в очередь", actor=f"{actor} · 🛠 инженер")
        await cb.answer("↩️ Вернул в очередь")
    elif result == "not_yours":
        await cb.answer("⛔ Это не твой заказ", show_alert=True)
    else:
        await cb.answer("Заказ не найден", show_alert=True); return
    back_page = int(page_s)
    if back_page < 0:
        text, kb = await views.render_mine_view(user, page=0)
    else:
        out = await views.render_queue_order_view(user, oid, back_page, bot)
        if not out:
            text, kb = await views.render_queue_view(user, back_page)
        else:
            text, kb = out
    try: await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except Exception: pass


@router.callback_query(F.data == "d:noop")
async def cb_dnoop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.message(Command("mine"))
@router.message(StateFilter(None), F.text.in_({"🧰 Мои заказы", "Мои заказы"}))
async def my_orders(msg: Message, user: User | None, bot: Bot) -> None:
    if not _allowed(user): return
    try: await bot.send_chat_action(msg.chat.id, "typing")
    except Exception: pass
    text, kb = await views.render_mine_view(user, page=0)
    await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("dm:p:"))
async def cb_mine_page(cb: CallbackQuery, user: User | None) -> None:
    if not _allowed(user):
        await cb.answer("🔒", show_alert=True); return
    page = int(cb.data.split(":")[2])
    text, kb = await views.render_mine_view(user, page=page)
    try: await cb.message.edit_text(text, reply_markup=kb)
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data.startswith("dm:o:"))
async def cb_mine_order(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _allowed(user):
        await cb.answer("🔒", show_alert=True); return
    _, _, oid_s, page_s = cb.data.split(":")
    out = await views.render_mine_order_view(user, int(oid_s), int(page_s), bot)
    if not out:
        await cb.answer("Заказ не найден", show_alert=True); return
    text, kb = out
    try: await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except Exception: pass
    await cb.answer()


@router.message(Command("req"))
async def cmd_req(msg: Message, user: User | None, state: FSMContext) -> None:
    if not _allowed(user): return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await msg.answer("Формат: <code>/req &lt;номер&gt;</code>, например <code>/req 12345</code>"); return
    await _start_request_for_number(msg, parts[1], state)


async def _collect_pick_orders(user: User) -> tuple[list, int]:
    """Return (orders, section_mine_count) — first N are user's own."""
    finished = await finished_status_ids()
    fdays = await fresh_days()
    async with session_scope() as s:
        mine = await repo.orders_assigned_to(
            s, user.id, exclude_status_ids=finished, limit=15, fresh_days=fdays,
        )
        q_rows = await repo.queue_page(
            s, offset=0, limit=20, exclude_status_ids=finished, fresh_days=fdays,
        )
    seen = {o.id for o in mine}
    rest = [o for (_q, o) in q_rows if o.id not in seen]
    orders = list(mine) + rest[: max(0, 20 - len(mine))]
    return orders, len(mine)


async def _render_order_picker(target: Message, user: User, *, edit: bool = False) -> None:
    orders, section_mine = await _collect_pick_orders(user)
    async with session_scope() as s:
        my_open = await repo.requests_by_user(
            s, user.id, statuses=[RequestStatus.open, RequestStatus.in_progress], limit=50,
        )
    open_n = len(my_open)
    if not orders:
        tail = f"\n\n<i>Твоих открытых запросов: {open_n} — см. «🗂 Мои запросы».</i>" if open_n else ""
        txt = (
            "📭 Нет подходящих заказов — ни своих, ни в очереди.\n"
            "Можно ввести номер вручную через /cancel и «🔎 Заказ»." + tail
        )
        if edit:
            try: await target.edit_text(txt); return
            except Exception: pass
        await target.answer(txt)
        return
    head_lines = ["📨 <b>Новый запрос</b>\nВыбери заказ — или найди по номеру."]
    if section_mine:
        head_lines.append(f"🧰 На тебе: <b>{section_mine}</b>")
    rest_n = len(orders) - section_mine
    if rest_n:
        head_lines.append(f"📋 Из очереди: <b>{rest_n}</b>")
    if open_n:
        head_lines.append(f"<i>Открытых твоих запросов: {open_n}</i>")
    text = "\n".join(head_lines)
    kb = order_pick_kb(orders, section_mine=section_mine)
    if edit:
        try: await target.edit_text(text, reply_markup=kb); return
        except Exception: pass
    await target.answer(text, reply_markup=kb)


@router.message(StateFilter(None), F.text.in_({"📨 Новый запрос", "Новый запрос"}))
async def new_request_button(msg: Message, user: User | None, state: FSMContext) -> None:
    if not _allowed(user): return
    await state.clear()
    await _render_order_picker(msg, user)


@router.callback_query(F.data == "eng:req:pickany")
async def cb_pick_any(cb: CallbackQuery, user: User | None) -> None:
    if not _allowed(user):
        await cb.answer("🔒"); return
    await _render_order_picker(cb.message, user, edit=True)
    await cb.answer()


async def _show_type_picker(target: Message, order, *, edit: bool = False) -> None:
    sn_line = f"\n🔢 {order.serial}" if order.serial else ""
    if getattr(order, "is_paid", False):
        text = (
            f"📨 <b>Запрос по #{order.number}</b>  ·  💰 Платный\n"
            f"📦 {order.device or '—'}{sn_line}\n\n"
            f"Платный ремонт — Асбис/ИТ4 не подходят.\n"
            f"• 💰 <b>Платному инженеру</b> — назначить кого-то из платных\n"
            f"• 🌐 <b>На аутсорс</b> — короткий комментарий обязателен\n"
            f"• ✏️ <b>Другое</b> — свободный текст"
        )
        kb = request_type_paid_kb(order.id)
    else:
        text = (
            f"📨 <b>Запрос по #{order.number}</b>  ·  🛡 Гарантия\n"
            f"📦 {order.device or '—'}{sn_line}\n\n"
            f"Что передать менеджеру?\n"
            f"• 🏷 <b>Асбис</b> — обслуживание по IMEI\n"
            f"• 📝 <b>ИТ4</b> — оформление\n"
            f"• ✏️ <b>Другое</b> — короткое сообщение"
        )
        kb = request_type_kb(order.id)
    if edit:
        try: await target.edit_text(text, reply_markup=kb); return
        except Exception: pass
    await target.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("eng:req:pick:"))
async def cb_pick_order(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _allowed(user):
        await cb.answer("🔒"); return
    oid = int(cb.data.split(":")[3])
    async with session_scope() as s:
        o = await repo.get_order(s, oid)
    if not o:
        await cb.answer("Заказ не найден", show_alert=True); return
    await state.clear()
    await _show_type_picker(cb.message, o, edit=True)
    await cb.answer()


@router.callback_query(F.data == "eng:req:bynum")
async def cb_pick_bynum(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _allowed(user):
        await cb.answer("🔒"); return
    await state.set_state(RequestFSM.waiting_order_no)
    prompt = (
        "🔎 Пришли <b>номер заказа</b> одним сообщением — например <code>12345</code>.\n"
        "/cancel — отмена."
    )
    try: await cb.message.edit_text(prompt)
    except Exception:
        await cb.message.answer(prompt)
    await cb.answer()


@router.message(RequestFSM.waiting_order_no)
async def on_request_order_no(msg: Message, user: User | None, state: FSMContext) -> None:
    if not _allowed(user):
        await state.clear(); return
    number = (msg.text or "").strip().lstrip("#")
    if not number:
        await msg.answer("Пустой номер. Пришли номер заказа или /cancel.")
        return
    async with session_scope() as s:
        o = await repo.get_order_by_number(s, number)
    if not o:
        await msg.answer(
            f"Не нашёл заказ <code>#{number}</code>. Попробуй ещё раз или /cancel."
        )
        return
    await state.clear()
    await _show_type_picker(msg, o)


async def _start_request_for_number(msg: Message, raw: str, state: FSMContext) -> None:
    number = raw.strip().lstrip("#")
    async with session_scope() as s:
        o = await repo.get_order_by_number(s, number)
    if not o:
        await msg.answer(f"Не нашёл заказ <code>#{number}</code>.")
        return
    await state.clear()
    await _show_type_picker(msg, o)


@router.callback_query(F.data.startswith("eng:take:"))
async def on_take(cb: CallbackQuery, user: User | None) -> None:
    if not _allowed(user):
        await cb.answer("🔒 Нет доступа", show_alert=True); return
    oid = int(cb.data.split(":")[2])
    from sqlalchemy import select
    from app.db.models import User as UM
    async with session_scope() as s:
        result = await repo.claim_order(s, oid, user.id)
        if result == "ok":
            order = await repo.get_order(s, oid)
            await repo.add_event(s, "order_taken", order_id=oid, payload={
                "number": order.number if order else oid,
                "engineer": user.full_name or str(user.tg_id),
            })
        elif result == "held":
            order = await repo.get_order(s, oid)
            holder = (await s.execute(
                select(UM).where(UM.id == (order.assigned_engineer_id if order else 0))
            )).scalar_one_or_none() if order else None
            who = holder.full_name if holder and holder.full_name else "другой инженер"
            await cb.answer(f"⛔ Уже у {who}", show_alert=True); return
    if result == "not_found":
        await cb.answer("Заказ не найден", show_alert=True); return
    if result == "already_mine":
        await cb.answer("Уже у тебя"); return
    await cb.answer("👤 Взял в работу")


@router.callback_query(F.data.startswith("eng:untake:"))
async def on_untake(cb: CallbackQuery, user: User | None) -> None:
    if not _allowed(user):
        await cb.answer("🔒 Нет доступа", show_alert=True); return
    oid = int(cb.data.split(":")[2])
    async with session_scope() as s:
        result = await repo.release_order(s, oid, user.id)
        if result == "ok":
            order = await repo.get_order(s, oid)
            await repo.add_event(s, "order_untaken", order_id=oid, payload={
                "number": order.number if order else oid,
                "engineer": user.full_name or str(user.tg_id),
            })
    if result == "not_found":
        await cb.answer("Заказ не найден", show_alert=True); return
    if result == "not_yours":
        await cb.answer("⛔ Это не твой заказ", show_alert=True); return
    await cb.answer("↩️ Вернул в очередь")


@router.callback_query(F.data.startswith("eng:reqmenu:"))
async def on_request_menu(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _allowed(user):
        await cb.answer("🔒 Нет доступа", show_alert=True); return
    oid = int(cb.data.split(":")[2])
    async with session_scope() as s:
        order = await repo.get_order(s, oid)
    if not order:
        await cb.answer("Заказ не найден", show_alert=True); return
    await state.clear()
    # If the card lives inside a widget message we edit in place; otherwise
    # we still try edit first and fall back to a new message.
    await _show_type_picker(cb.message, order, edit=True)
    await cb.answer()


# --- paid-engineer pick ---
@router.callback_query(F.data.startswith("eng:paid:assign:"))
async def on_paid_assign_pick(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    """Engineer chooses which paid engineer to forward the order to."""
    if not _allowed(user):
        await cb.answer("🔒"); return
    oid = int(cb.data.split(":")[3])
    async with session_scope() as s:
        order = await repo.get_order(s, oid)
        engineers = await repo.list_paid_engineers(s)
    if not order:
        await cb.answer("Заказ не найден", show_alert=True); return
    # Drop self from the list — choosing yourself makes no sense.
    engineers = [e for e in engineers if e.id != user.id]
    if not engineers:
        await cb.answer(
            "Нет других платных инженеров. Включи флаг в админке → Пользователи.",
            show_alert=True,
        ); return
    text = (
        f"💰 <b>Кому из платных передать #{order.number}?</b>\n"
        f"📦 {order.device or '—'}\n\n"
        f"<i>Выбери инженера — он получит push в личку и сможет «взять» заказ.</i>"
    )
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    for e in engineers:
        nm = e.full_name or (e.username and f"@{e.username}") or str(e.tg_id)
        b.button(text=f"💰 {nm}", callback_data=f"eng:paid:to:{e.id}:{oid}")
    b.button(text="⬅️ Отмена", callback_data=f"eng:reqmenu:{oid}")
    b.adjust(*([1] * len(engineers) + [1]))
    try: await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await cb.message.answer(text, reply_markup=b.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("eng:paid:to:"))
async def on_paid_assign_apply(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _allowed(user):
        await cb.answer("🔒"); return
    parts = cb.data.split(":")
    eid = int(parts[3]); oid = int(parts[4])
    from sqlalchemy import select
    from app.db.models import User as UM
    async with session_scope() as s:
        target = (await s.execute(select(UM).where(UM.id == eid))).scalar_one_or_none()
        if not target or not target.is_active or not target.is_paid_engineer:
            await cb.answer("Платный инженер не найден", show_alert=True); return
        status, prev = await repo.force_assign_order(s, oid, eid)
        order = await repo.get_order(s, oid)
        if status == "ok":
            await repo.add_event(s, "order_assigned", order_id=oid, payload={
                "number": order.number if order else oid,
                "engineer_id": eid,
                "engineer": target.full_name or str(target.tg_id),
                "prev_engineer_id": prev,
                "by": user.full_name or str(user.tg_id),
                "via": "paid",
            })
    if status == "noop":
        await cb.answer("Уже на нём"); return
    if status == "not_found":
        await cb.answer("Заказ не найден", show_alert=True); return
    actor = user.full_name or (user.username and f"@{user.username}") or str(user.tg_id)
    new_name = target.full_name or (target.username and f"@{target.username}") or str(target.tg_id)
    from app.bot.services.sync_ro import push_action_comment
    await push_action_comment(
        oid, "💰 Передан платному инженеру",
        actor=f"{actor} · 🛠 инженер",
        detail=f"назначен: {new_name}",
    )
    # DM to the new owner — event-driven heads-up.
    try:
        from app.bot.services.deep_link import order_deep_link
        dl = await order_deep_link(bot, oid)
    except Exception:
        dl = ""
    num = order.number if order else oid
    dev_line = f" · {order.device}" if order and order.device else ""
    try:
        await bot.send_message(
            target.tg_id,
            f"💰 <b>На тебя передали платный заказ</b>\n"
            f"📦 <b>#{num}</b>{dev_line}\n"
            f"Передал: {actor}\n\n"
            f"<a href='{dl}'>Открыть в боте</a>",
            disable_web_page_preview=True,
        )
    except Exception:
        pass
    try:
        await cb.message.edit_text(
            f"✅ <b>#{num}</b> передан: {new_name}.\n"
            f"<i>Платный инженер получил уведомление в личку.</i>"
        )
    except Exception:
        pass
    await cb.answer("✅ Передано")


@router.callback_query(F.data == "eng:reqcancel")
async def on_request_cancel_menu(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _allowed(user):
        await cb.answer("🔒", show_alert=True); return
    await state.clear()
    try: await cb.message.delete()
    except Exception:
        try: await cb.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
    await cb.answer("❎ Отменено")


def _auto_payload(order, rtype: RequestType) -> str:
    """Build default payload from order data for no-text request types."""
    dev = (order.device or "").strip() if order else ""
    sn  = (order.serial or "").strip() if order else ""
    if rtype == RequestType.asbis:
        head = "🏷 Асбис — обслуживание"
        if sn:
            return f"{head}\nIMEI/SN: {sn}" + (f"\n{dev}" if dev else "")
        return f"{head}" + (f"\n{dev}" if dev else "")
    if rtype == RequestType.it4:
        head = "📝 ИТ4 — оформление"
        return f"{head}" + (f"\n{dev}" if dev else "")
    return "—"


@router.callback_query(F.data.startswith("eng:req:"))
async def on_request_start(cb: CallbackQuery, user: User | None, state: FSMContext, bot: Bot) -> None:
    if not _allowed(user):
        await cb.answer("🔒 Нет доступа", show_alert=True); return
    parts = cb.data.split(":")
    # eng:req:customok:<oid> — подтверждение «Другое» без текста
    if len(parts) >= 4 and parts[2] == "customok":
        try: oid = int(parts[3])
        except Exception:
            await cb.answer("?"); return
        await state.clear()
        async with session_scope() as s:
            order = await repo.get_order(s, oid)
        dev = (order.device or "—").strip() if order else "—"
        payload = f"✏️ Без описания\n{dev}"
        await _finalize_request(cb.message, user, bot,
                                oid=oid, rtype=RequestType.custom, payload=payload, edit=True)
        await cb.answer("✅ Отправлено")
        return

    _, _, rtype_s, order_id = parts
    try: rtype = RequestType(rtype_s)
    except Exception:
        await cb.answer("?"); return
    oid = int(order_id)

    # Запрет ASBIS/IT4 на платных заказах — это правило бизнес-процесса.
    if rtype in (RequestType.asbis, RequestType.it4):
        async with session_scope() as s:
            order = await repo.get_order(s, oid)
        if order and order.is_paid:
            await cb.answer(
                "💰 Платный ремонт — ASBIS/ИТ4 недоступны. Используй «Платному инженеру» или «На аутсорс».",
                show_alert=True,
            )
            return
        await state.clear()
        payload = _auto_payload(order, rtype)
        await _finalize_request(cb.message, user, bot,
                                oid=oid, rtype=rtype, payload=payload, edit=True)
        await cb.answer("✅ Отправлено")
        return

    # Для «Другое»/«Аутсорс» — просим короткий текст прямо в том же сообщении.
    await state.set_state(RequestFSM.waiting_text)
    await state.update_data(rtype=rtype.value, order_id=oid)
    prompt = REQ_PROMPT.get(rtype.value, "Опиши запрос. /cancel — отмена.")
    # Для аутсорса — комментарий обязателен (правило бизнес-процесса).
    allow_empty = (rtype != RequestType.outsource)
    kb = eng_custom_text_kb(oid, allow_empty=allow_empty)
    try:
        await cb.message.edit_text(prompt, reply_markup=kb)
    except Exception:
        try: await cb.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
        await cb.message.answer(prompt, reply_markup=kb)
    await cb.answer()


@router.message(RequestFSM.waiting_text)
async def on_request_text(msg: Message, user: User | None, state: FSMContext, bot: Bot) -> None:
    if not _allowed(user):
        await state.clear(); return
    data = await state.get_data()
    oid = int(data["order_id"])
    rtype = RequestType(data["rtype"])
    text = (msg.text or "").strip()
    if rtype == RequestType.outsource and not text:
        await msg.answer("🌐 Для аутсорса нужен комментарий — кому передаём и зачем. /cancel — отмена.")
        return
    await state.clear()
    await _finalize_request(msg, user, bot, oid=oid, rtype=rtype, payload=text or "—")


async def _finalize_request(out: Message, user: User, bot: Bot, *,
                             oid: int, rtype: RequestType, payload: str,
                             edit: bool = False) -> None:
    """Create request, push RO comment, notify managers, confirm to author.

    When `edit=True`, the confirmation is written over `out` (used from DM
    widgets so we don't leave a stale card + a new message).
    """
    async with session_scope() as s:
        order = await repo.get_order(s, oid)
        req = await repo.create_request(s, oid, rtype, payload, created_by=user.id)
        await repo.add_event(s, "request_created", order_id=oid, payload={
            "number": order.number if order else oid, "type": rtype.value,
            "payload": payload, "rid": req.id,
        })
        mgrs = await repo.list_users(s, role=Role.manager)
        mgr_ids = [u.tg_id for u in mgrs if u.is_active]
    await push_request_comment(oid, rtype.value, payload,
                               author=user.full_name or str(user.tg_id))

    number = order.number if order else oid
    device = (order.device or "—").strip() if order else "—"
    serial = (order.serial or "").strip() if order else ""
    type_label = REQUEST_LABELS.get(rtype.value, rtype.value)
    author = user.full_name or (user.username and f"@{user.username}") or str(user.tg_id)
    sn_line = f"\n🔢 {serial}" if serial else ""
    dm_text = (
        f"📨 <b>Новый запрос #{req.id}</b> · {type_label}\n"
        f"📦 <b>#{number}</b> — {device}{sn_line}\n"
        f"👤 от {author}\n\n"
        f"<i>{payload}</i>"
    )
    for tg_id in mgr_ids:
        try:
            await bot.send_message(tg_id, dm_text,
                                   reply_markup=request_card_kb(req.id, rtype=rtype.value))
        except Exception:
            pass
    mgr_word = "менеджеру" if len(mgr_ids) == 1 else f"менеджерам ({len(mgr_ids)})"
    confirm_text = (
        f"✅ <b>Запрос #{req.id}</b> по <b>#{number}</b> ушёл {mgr_word}.\n"
        f"Статус: {human_request_status(rtype.value, 'open')}.\n"
        f"Как только ответят — пришлю сюда.\n"
        f"<i>Передумал? Нажми «❎ Отменить запрос».</i>"
    )
    kb = my_request_kb(req.id, open_state=True)
    if edit:
        try: await out.edit_text(confirm_text, reply_markup=kb); return
        except Exception: pass
    await out.answer(confirm_text, reply_markup=kb)


@router.message(Command("myreq"))
@router.message(StateFilter(None), F.text.in_({"🗂 Мои запросы", "Мои запросы"}))
async def my_requests(msg: Message, user: User | None) -> None:
    if not _allowed(user): return
    text, kb = await views.render_myreq_view(user, flt="open")
    await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("dr:f:"))
async def cb_myreq_filter(cb: CallbackQuery, user: User | None) -> None:
    if not _allowed(user):
        await cb.answer("🔒", show_alert=True); return
    flt = cb.data.split(":")[2]
    text, kb = await views.render_myreq_view(user, flt=flt)
    try: await cb.message.edit_text(text, reply_markup=kb)
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data.startswith("eng:reqown:cancel:"))
async def cb_own_cancel(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _allowed(user):
        await cb.answer("🔒"); return
    rid = int(cb.data.split(":")[3])
    async with session_scope() as s:
        r = await repo.get_request(s, rid)
        if not r:
            await cb.answer("Запрос не найден", show_alert=True); return
        if r.created_by != user.id:
            await cb.answer("⛔ Это не твой запрос", show_alert=True); return
        if r.status not in (RequestStatus.open, RequestStatus.in_progress):
            await cb.answer("Уже закрыт", show_alert=True); return
        order = await repo.get_order(s, r.order_id)
        await repo.set_request_status(s, rid, RequestStatus.rejected, assigned_to=user.id)
        await repo.add_event(s, "request_status", order_id=r.order_id, payload={
            "rid": rid, "number": order.number if order else r.order_id,
            "status": "rejected", "rtype": r.type.value, "note": "cancelled_by_author",
        })
        mgrs = await repo.list_users(s, role=Role.manager)
        mgr_ids = [u.tg_id for u in mgrs if u.is_active]
    number = order.number if order else r.order_id
    type_label = REQUEST_LABELS.get(r.type.value, r.type.value)
    author = user.full_name or (user.username and f"@{user.username}") or str(user.tg_id)
    note_for_mgr = (
        f"↩️ <b>Запрос #{rid}</b> · {type_label} · <b>#{number}</b>\n"
        f"Отменил автор ({author}). Ничего делать не нужно."
    )
    for tg in mgr_ids:
        try: await bot.send_message(tg, note_for_mgr)
        except Exception: pass
    await push_request_status_comment(r.order_id, r.type.value, "cancelled_by_author",
                                      note=None, actor=author)
    # If this cancel came from the "my requests" widget, refresh it in place.
    try:
        text, kb = await views.render_myreq_view(user, flt="open")
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
            base = cb.message.html_text or cb.message.text or ""
            await cb.message.edit_text(base + "\n\n↩️ <b>Отменён автором</b>")
        except Exception: pass
    await cb.answer("↩️ Отменил")
