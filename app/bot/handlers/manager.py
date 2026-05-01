from __future__ import annotations
from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.db.session import session_scope
from app.db import repo
from app.db.models import Role, User, RequestStatus, RequestType
from app.bot.texts import REQUEST_LABELS, human_request_status
from app.bot.keyboards import mgr_reject_cancel_kb, reject_reasons_kb, REJECT_REASONS
from app.bot.services.deep_link import order_deep_link
from app.bot.services.sync_ro import push_request_status_comment
from app.bot import views

router = Router(name="manager")


def _allowed(user: User | None) -> bool:
    return bool(user and user.is_active and user.role in (Role.manager, Role.admin, Role.owner))

def _allowed_to_write(user: User | None) -> bool:
    return bool(user and user.is_active and user.role in (Role.manager, Role.admin))


class RejectFSM(StatesGroup):
    waiting_comment = State()


def _extract_ids(payload: str) -> list[str]:
    """Extract IMEI/SN-like tokens from text; fallback to first non-empty lines."""
    import re
    tokens: list[str] = []
    for raw in (payload or "").replace(",", " ").split():
        t = raw.strip().strip(";:()[]{}")
        if not t:
            continue
        if re.fullmatch(r"\d{14,17}", t) or re.fullmatch(r"[A-Za-z0-9\-]{8,32}", t):
            tokens.append(t.upper())
    if tokens:
        return tokens
    lines = [ln.strip() for ln in (payload or "").splitlines() if ln.strip()]
    return lines[:3]


@router.message(Command("inbox"))
@router.message(StateFilter(None), F.text.in_({"📨 Входящие", "Входящие", "📨 Входящие запросы", "Входящие запросы"}))
async def inbox(msg: Message, user: User | None, bot: Bot) -> None:
    if not _allowed(user): return
    try: await bot.send_chat_action(msg.chat.id, "typing")
    except Exception: pass
    text, kb = await views.render_inbox_view(user, flt="all", page=0)
    await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("di:f:"))
async def cb_inbox_filter(cb: CallbackQuery, user: User | None) -> None:
    if not _allowed(user):
        await cb.answer("🔒", show_alert=True); return
    _, _, flt, page_s = cb.data.split(":")
    text, kb = await views.render_inbox_view(user, flt=flt, page=int(page_s))
    try: await cb.message.edit_text(text, reply_markup=kb)
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data.startswith("di:o:"))
async def cb_inbox_open(cb: CallbackQuery, user: User | None) -> None:
    if not _allowed(user):
        await cb.answer("🔒", show_alert=True); return
    _, _, rid_s, flt, page_s = cb.data.split(":")
    out = await views.render_inbox_request_view(user, int(rid_s), flt, int(page_s))
    if not out:
        await cb.answer("Запрос не найден", show_alert=True); return
    text, kb = out
    try: await cb.message.edit_text(text, reply_markup=kb)
    except Exception: pass
    await cb.answer()


@router.message(StateFilter(None), F.text.in_({"📋 Асбис", "Асбис"}))
async def list_asbis(msg: Message, user: User | None) -> None:
    if not _allowed(user): return
    text, kb = await views.render_inbox_view(user, flt="asbis", page=0)
    await msg.answer(text, reply_markup=kb)


@router.message(StateFilter(None), F.text.in_({"📋 ИТ4", "ИТ4"}))
async def list_it4(msg: Message, user: User | None) -> None:
    if not _allowed(user): return
    text, kb = await views.render_inbox_view(user, flt="it4", page=0)
    await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("mgr:list:"))
async def list_actions(cb: CallbackQuery, user: User | None) -> None:
    if not _allowed(user):
        await cb.answer("🔒 Нет доступа", show_alert=True); return
    parts = cb.data.split(":")
    # mgr:list:export:<type>
    if len(parts) == 4 and parts[2] == "export":
        t = parts[3]
        if t not in ("asbis", "it4"):
            await cb.answer("?"); return
        rtype = RequestType(t)
        async with session_scope() as s:
            rows = await repo.open_requests(s, rtype=rtype)
        if not rows:
            await cb.answer("Пусто", show_alert=True); return
        if rtype == RequestType.asbis:
            lines: list[str] = []
            for r, o in rows:
                ids = _extract_ids(r.payload_text or "")
                if not ids:
                    lines.append(f"#{o.number} — IMEI/SN не указан")
                else:
                    for ident in ids:
                        lines.append(f"{ident}  ·  #{o.number}")
            await cb.message.answer(
                "📤 <b>Выгрузка Асбис</b>\n<code>" + "\n".join(lines) + "</code>"
            )
        else:
            lines = [
                f"#{o.number} — {(r.payload_text or '—').splitlines()[0][:80]}"
                for r, o in rows
            ]
            await cb.message.answer("📤 <b>Выгрузка ИТ4</b>\n" + "\n".join(lines))
        await cb.answer("Сформировано")
        return
    # mgr:list:<type> — just refresh the widget filter
    t = parts[2]
    if t not in ("asbis", "it4"):
        await cb.answer("?"); return
    text, kb = await views.render_inbox_view(user, flt=t, page=0)
    try: await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer("Обновлено")


@router.callback_query(F.data.startswith("mgr:req:rejc:cancel:"))
async def cancel_reject_comment(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _allowed_to_write(user):
        await cb.answer("🔒 У вас режим только для чтения", show_alert=True); return
    await state.clear()
    try: await cb.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    await cb.answer("Отмена")


@router.callback_query(F.data.startswith("mgr:req:rejr:"))
async def reject_with_preset(cb: CallbackQuery, user: User | None, bot: Bot, state: FSMContext) -> None:
    """Reject request using a preset reason code."""
    if not _allowed_to_write(user):
        await cb.answer("🔒 У вас режим только для чтения", show_alert=True); return
    _, _, _, rid_s, code = cb.data.split(":")
    rid = int(rid_s)
    note = next((full for c, _lbl, full in REJECT_REASONS if c == code), None)
    if not note:
        await cb.answer("Неизвестная причина"); return
    await state.clear()
    try: await cb.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    await _apply_status(cb, user, bot, rid=rid, new_status=RequestStatus.rejected, note=note)
    await cb.answer("🚫 Отклонено")


@router.callback_query(F.data.startswith("mgr:req:rejtxt:"))
async def reject_with_custom_text(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    """Switch to FSM and wait for free-text reject comment."""
    if not _allowed_to_write(user):
        await cb.answer("🔒 У вас режим только для чтения", show_alert=True); return
    rid = int(cb.data.split(":")[3])
    await state.set_state(RejectFSM.waiting_comment)
    await state.update_data(rid=rid)
    prompt = (
        f"✏️ <b>Причина отказа по запросу #{rid}</b>\n"
        "Напиши своими словами — текст уйдёт инженеру и останется комментарием в RemOnline."
    )
    kb = mgr_reject_cancel_kb(rid)
    try:
        await cb.message.edit_text(prompt, reply_markup=kb)
    except Exception:
        try: await cb.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
        await cb.message.answer(prompt, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("mgr:req:rejc:"))
async def ask_reject_comment(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    """Show preset reject reasons; user can still choose «Свой текст»."""
    if not _allowed_to_write(user):
        await cb.answer("🔒 У вас режим только для чтения", show_alert=True); return
    rid = int(cb.data.split(":")[3])
    await state.clear()
    prompt = (
        f"🚫 <b>Причина отказа по запросу #{rid}</b>\n"
        "Выбери готовую формулировку или напиши свою."
    )
    kb = reject_reasons_kb(rid)
    try:
        await cb.message.edit_text(prompt, reply_markup=kb)
    except Exception:
        try: await cb.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
        await cb.message.answer(prompt, reply_markup=kb)
    await cb.answer()


@router.message(RejectFSM.waiting_comment)
async def on_reject_comment(msg: Message, user: User | None, state: FSMContext, bot: Bot) -> None:
    if not _allowed(user):
        await state.clear(); return
    note = (msg.text or "").strip()
    if not note:
        await msg.answer("Нужен текст причины. Или нажми «⬅️ Отмена».")
        return
    data = await state.get_data()
    rid = int(data.get("rid") or 0)
    await state.clear()
    await _apply_status(msg, user, bot, rid=rid, new_status=RequestStatus.rejected, note=note)
    await msg.answer(f"🚫 Запрос #{rid} отклонён.")


@router.callback_query(F.data.startswith("mgr:req:"))
async def on_change(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    """Apply 'in progress' or 'done' to a request (reject is handled separately via presets)."""
    if not _allowed_to_write(user):
        await cb.answer("🔒 У вас режим только для чтения", show_alert=True); return
    _, _, action, rid_s = cb.data.split(":")
    rid = int(rid_s)
    status_map = {"ip": RequestStatus.in_progress, "done": RequestStatus.done}
    new_status = status_map.get(action)
    if not new_status:
        await cb.answer("?"); return
    await _apply_status(cb, user, bot, rid=rid, new_status=new_status, note=None)
    human_short = {
        RequestStatus.in_progress: "⚙️ Взял в работу",
        RequestStatus.done: "✅ Готово",
    }[new_status]
    await cb.answer(human_short)


async def _apply_status(target: CallbackQuery | Message,
                        user: User,
                        bot: Bot,
                        *,
                        rid: int,
                        new_status: RequestStatus,
                        note: str | None,
                        ) -> None:
    creator_tg = None
    req_type = None
    order_number = None
    order_id_for_link: int | None = None
    payload_text = ""
    async with session_scope() as s:
        r = await repo.set_request_status(s, rid, new_status, assigned_to=user.id)
        if r:
            order = await repo.get_order(s, r.order_id)
            order_number = order.number if order else r.order_id
            order_id_for_link = r.order_id
            req_type = r.type.value
            payload_text = (r.payload_text or "").strip()
            await repo.add_event(s, "request_status", order_id=r.order_id, payload={
                "rid": rid, "number": order_number, "status": new_status.value,
                "rtype": req_type, "note": note or "",
            })
            if r.created_by:
                from sqlalchemy import select
                from app.db.models import User as UM
                creator = (await s.execute(select(UM).where(UM.id == r.created_by))).scalar_one_or_none()
                creator_tg = creator.tg_id if creator else None
    actor = user.full_name or (user.username and f"@{user.username}") or str(user.tg_id)
    if order_id_for_link is not None and req_type:
        await push_request_status_comment(order_id_for_link, req_type, new_status.value, note=note, actor=f"{actor} · 📞 менеджер")
        # ASBIS/IT4 rejected → auto-move RO status to "Выдача без ремонта" if configured.
        if (req_type in ("asbis", "it4")
                and new_status == RequestStatus.rejected
                and order_id_for_link):
            from app.bot.services.settings_cache import auto_status_no_repair_id
            status_id = await auto_status_no_repair_id()
            if status_id:
                try:
                    from app.remonline.client import RemOnlineClient, RemOnlineError
                    async with RemOnlineClient() as ro:
                        await ro.update_order(order_id_for_link, {"status_id": status_id})
                    from app.bot.services.sync_ro import push_action_comment
                    await push_action_comment(order_id_for_link,
                                              "Авто-статус: «Выдача без ремонта»",
                                              actor=f"{actor} · 📞 менеджер",
                                              detail=f"причина отказа [{req_type}]: {note or '—'}")
                except RemOnlineError:
                    pass
    if creator_tg and order_id_for_link is not None:
        try:
            dl = await order_deep_link(bot, order_id_for_link)
            snippet = payload_text.splitlines()[0][:140] if payload_text else ""
            snippet_line = f"\n<i>{snippet}</i>" if snippet else ""
            note_line = (f"\n🚫 Причина: <i>{note}</i>"
                         if (new_status == RequestStatus.rejected and note) else "")
            human = human_request_status(req_type, new_status.value, note)
            # «Менеджер подтвердил ремонт по ASBIS — можно начинать»
            if (new_status == RequestStatus.done
                    and req_type in ("asbis", "it4")):
                req_label = REQUEST_LABELS.get(req_type or "", req_type or "")
                head = f"✅ <b>Менеджер подтвердил {req_label} — можно начинать</b>"
            elif new_status == RequestStatus.rejected:
                head = f"🚫 <b>Запрос #{rid} отклонён</b>"
            elif new_status == RequestStatus.in_progress:
                head = f"⚙️ <b>Менеджер взял запрос #{rid} в работу</b>"
            else:
                head = f"📬 <b>Ответ по запросу #{rid}</b>"
            dm = (
                f"{head}\n"
                f"Заказ: <b>#{order_number}</b> · {REQUEST_LABELS.get(req_type or '', req_type or '')}\n"
                f"Статус: {human}\n"
                f"Менеджер: {actor}"
                f"{snippet_line}{note_line}\n\n"
                f"<a href='{dl}'>Открыть заказ</a>"
            )
            await bot.send_message(creator_tg, dm, disable_web_page_preview=True)
        except Exception:
            pass
    message = target.message if isinstance(target, CallbackQuery) else target
    base = getattr(message, "html_text", None) or message.text or ""
    final = new_status in (RequestStatus.done, RequestStatus.rejected)
    human = human_request_status(req_type, new_status.value, note)
    note_append = (f"\n🚫 Причина: <i>{note}</i>"
                   if (new_status == RequestStatus.rejected and note) else "")
    # If we're inside the inbox widget (card or reject-preset screen), swap
    # to the refreshed inbox list to avoid leaving a dangling, read-only card.
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    is_widget = (
        base.startswith("📨 <b>Запрос #")
        or base.startswith("🚫 <b>Причина отказа")
        or base.startswith("✏️ Свой текст отказа")
    )
    if is_widget and final:
        text, kb = await views.render_inbox_view(user, flt="all", page=0)
        try: await message.edit_text(text, reply_markup=kb)
        except Exception: pass
        return
    if not final:
        b = InlineKeyboardBuilder()
        if req_type in ("asbis", "it4"):
            b.button(text="✅ Подтвердить",       callback_data=f"mgr:req:done:{rid}")
            b.button(text="🚫 Отклонить с комм.", callback_data=f"mgr:req:rejc:{rid}")
            rows = [2]
        else:
            b.button(text="⚙️ В работу", callback_data=f"mgr:req:ip:{rid}")
            b.button(text="✅ Готово",    callback_data=f"mgr:req:done:{rid}")
            b.button(text="🚫 Отклонить с комм.", callback_data=f"mgr:req:rejc:{rid}")
            rows = [2, 1]
        if is_widget:
            b.button(text="⬅️ Входящие", callback_data="di:f:all:0")
            rows.append(1)
        b.adjust(*rows)
        kb = b.as_markup()
    else:
        kb = None
    try:
        await message.edit_text(
            f"{base}\n\n→ {human}{note_append}",
            reply_markup=kb,
        )
    except Exception:
        pass
