from __future__ import annotations
import asyncio
from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db import repo
from app.db.models import Role, User, QueueItem
from app.bot.texts import ROLE_LABELS
from app.bot.keyboards import (
    admin_menu_kb, admin_settings_kb, back_kb, finished_toggle_kb,
    role_picker_kb, main_menu, invite_menu_kb, invite_role_pick_kb,
    confirm_kb, sla_days_kb, fresh_days_kb, auto_no_repair_kb,
)
from app.bot.services.invites import create_invite
from app.bot.services.settings_cache import (
    finished_status_ids, set_finished_status_ids, sla_days, fresh_days, set_fresh_days,
    auto_status_no_repair_id, set_auto_status_no_repair_id,
    paid_marker, set_paid_marker,
)

router = Router(name="admin")


class AdminFSM(StatesGroup):
    broadcast = State()
    invite_tg_id = State()
    wipe_count = State()
    paid_kind_ids = State()
    paid_name_subs = State()


def _is_admin(user: User | None, tg_id: int | None) -> bool:
    real_role = getattr(user, "real_role", None)
    if user and user.is_active and (user.role == Role.admin or real_role == Role.admin):
        return True
    return tg_id is not None and tg_id in settings.admin_ids


def _impersonating_label(user: User | None) -> str | None:
    if not user:
        return None
    real = getattr(user, "real_role", None)
    if real and real != user.role:
        return f"🧪 Режим теста: {ROLE_LABELS[user.role.value]} (база: {ROLE_LABELS[real.value]})"
    return None


async def _render_admin_menu(target: Message | CallbackQuery, user: User | None,
                              edit: bool = False) -> None:
    head = "🛡 <b>Админ-панель</b>"
    imp = _impersonating_label(user)
    if imp:
        head += f"\n\n{imp}"
    kb = admin_menu_kb()
    if edit and isinstance(target, CallbackQuery):
        try: await target.message.edit_text(head, reply_markup=kb); return
        except Exception: pass
    send = target.message.answer if isinstance(target, CallbackQuery) else target.answer
    await send(head, reply_markup=kb)


# ---------- entry ----------
@router.message(Command("admin"))
@router.message(StateFilter(None), F.text.in_({"🛡 Админ", "Админ"}))
async def admin_menu(msg: Message, user: User | None) -> None:
    if not _is_admin(user, msg.from_user.id if msg.from_user else None): return
    await _render_admin_menu(msg, user)


@router.callback_query(F.data == "adm:back")
async def cb_back(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    await _render_admin_menu(cb, user, edit=True)
    await cb.answer()


@router.callback_query(F.data == "adm:asrole")
async def cb_asrole_menu(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒 Нет доступа", show_alert=True); return
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    for role, label in [
        (Role.admin, "🛡 Админ"),
        (Role.reception, "🛎 Приёмка"),
        (Role.engineer, "🛠 Инженер"),
        (Role.manager, "📋 Менеджер"),
    ]:
        mark = "✅ " if user and user.role == role else ""
        b.button(text=f"{mark}{label}", callback_data=f"adm:asrole:set:{role.value}")
    b.button(text="♻️ Сбросить режим теста", callback_data="adm:asrole:reset")
    b.button(text="⬅️ Назад", callback_data="adm:back")
    b.adjust(2, 2, 1, 1)
    text = (
        "🧪 <b>Тестовый режим ролей</b>\n"
        "Выбери роль, в которой открыть меню и кнопки.\n\n"
        "<i>Это не меняет базовую роль пользователя в БД.</i>"
    )
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await cb.message.answer(text, reply_markup=b.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("adm:asrole:set:"))
async def cb_asrole_set(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒 Нет доступа", show_alert=True); return
    role = Role(cb.data.rsplit(":", 1)[1])
    async with session_scope() as s:
        await repo.set_role_override(s, cb.from_user.id, role)
    await cb.answer(f"✅ Режим: {ROLE_LABELS[role.value]}")

    from app.bot.commands import apply_commands_for_user
    from app.bot.keyboards import main_menu
    await apply_commands_for_user(bot, cb.from_user.id, role)

    try: await cb.message.delete()
    except Exception: pass

    await cb.message.answer(
        f"🧪 Роль переключена на <b>{ROLE_LABELS[role.value]}</b>.\n"
        "Меню кнопок и команд обновлено.",
        reply_markup=main_menu(role)
    )


@router.callback_query(F.data == "adm:asrole:reset")
async def cb_asrole_reset(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒 Нет доступа", show_alert=True); return
    async with session_scope() as s:
        await repo.set_role_override(s, cb.from_user.id, None)
    await cb.answer("✅ Сброшено")

    from app.bot.commands import apply_commands_for_user
    from app.bot.keyboards import main_menu
    real_role = getattr(user, "real_role", user.role if user else Role.admin)
    await apply_commands_for_user(bot, cb.from_user.id, real_role)

    try: await cb.message.delete()
    except Exception: pass

    await cb.message.answer(
        "Режим теста сброшен. Меню админа возвращено.",
        reply_markup=main_menu(real_role)
    )


# ---------- users ----------
def _user_row_kb(u: User) -> InlineKeyboardMarkup:
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    for role, label in [(Role.reception, "🛎"), (Role.engineer, "🛠"),
                        (Role.manager, "📋"), (Role.admin, "🛡")]:
        if u.role != role:
            b.button(text=label, callback_data=f"adm:setrole:{u.tg_id}:{role.value}")
    if u.is_active:
        b.button(text="🚫 off", callback_data=f"adm:setrole:{u.tg_id}:deny")
    else:
        b.button(text="♻️ on", callback_data=f"adm:setrole:{u.tg_id}:{u.role.value}")
    sizes = [4]
    if u.role == Role.engineer:
        paid_label = "💰 Платный: ВКЛ" if u.is_paid_engineer else "💰 Платный: выкл"
        b.button(text=paid_label, callback_data=f"adm:paid:{u.tg_id}")
        sizes.append(1)
    b.adjust(*sizes)
    return b.as_markup()


@router.callback_query(F.data == "adm:users")
async def admin_users(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒 Нет доступа", show_alert=True); return
    async with session_scope() as s:
        users = await repo.list_users(s)
    head = (f"👥 <b>Пользователи</b> · всего {len(users)}\n"
            "<i>Жми иконку роли, чтобы поменять. «🚫» отключит, «♻️» вернёт.</i>")
    try:
        await cb.message.edit_text(head, reply_markup=back_kb())
    except Exception:
        await cb.message.answer(head)
    if not users:
        await cb.message.answer("<i>Пользователей ещё нет.</i>")
    for u in users:
        status = "🟢" if u.is_active else "🔴"
        text = (f"{status} <b>{u.full_name or '—'}</b> @{u.username or '—'}\n"
                f"{ROLE_LABELS[u.role.value]}  ·  <code>{u.tg_id}</code>")
        await cb.message.answer(text, reply_markup=_user_row_kb(u))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:paid:"))
async def cb_toggle_paid_engineer(cb: CallbackQuery, user: User | None) -> None:
    """Toggle is_paid_engineer for an engineer (admin-only)."""
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒 Нет доступа", show_alert=True); return
    tg_id_int = int(cb.data.split(":")[2])
    async with session_scope() as s:
        u = await repo.get_user_by_tg(s, tg_id_int)
        if not u or u.role != Role.engineer:
            await cb.answer("Только для инженеров", show_alert=True); return
        await repo.set_user_paid_engineer(s, tg_id_int, not u.is_paid_engineer)
        u = await repo.get_user_by_tg(s, tg_id_int)
    status = "🟢" if u.is_active else "🔴"
    text = (f"{status} <b>{u.full_name or '—'}</b> @{u.username or '—'}\n"
            f"{ROLE_LABELS[u.role.value]}  ·  <code>{u.tg_id}</code>")
    try: await cb.message.edit_text(text, reply_markup=_user_row_kb(u))
    except Exception: pass
    await cb.answer("✅ Платный" if u.is_paid_engineer else "✅ Снят флаг")


@router.callback_query(F.data.startswith("adm:setrole:"))
async def cb_set_role(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒 Нет доступа", show_alert=True); return
    _, _, tg_id, action = cb.data.split(":")
    tg_id_int = int(tg_id)

    if action == "deny" and user and user.tg_id == tg_id_int:
        await cb.answer("Нельзя отключить самого себя.", show_alert=True); return

    released: list[int] = []
    if action == "deny":
        async with session_scope() as s:
            admins = await repo.list_users(s, role=Role.admin)
            target = await repo.get_user_by_tg(s, tg_id_int)
            if target and target.role == Role.admin:
                if len([a for a in admins if a.is_active]) <= 1:
                    await cb.answer("Это последний активный админ.", show_alert=True); return
            await repo.set_user_active(s, tg_id_int, False)
            await repo.set_role_override(s, tg_id_int, None)
            if target:
                released = await repo.release_orders_of_user(s, target.id)
    else:
        try: role = Role(action)
        except Exception:
            await cb.answer("Неизвестная роль"); return
        real_role = getattr(user, "real_role", user.role if user else None)
        if user and user.tg_id == tg_id_int and real_role == Role.admin and role != Role.admin:
            async with session_scope() as s:
                admins = await repo.list_users(s, role=Role.admin)
            if len([a for a in admins if a.is_active]) <= 1:
                await cb.answer("Это последний активный админ.", show_alert=True); return
        async with session_scope() as s:
            target_before = await repo.get_user_by_tg(s, tg_id_int)
            await repo.upsert_user(s, tg_id_int, role)
            if role != Role.admin:
                await repo.set_role_override(s, tg_id_int, None)
            if target_before and target_before.role == Role.engineer and role != Role.engineer:
                released = await repo.release_orders_of_user(s, target_before.id)
        try:
            await bot.send_message(
                tg_id_int,
                f"🔔 Тебе выдана роль: <b>{ROLE_LABELS[role.value]}</b>.\n"
                f"Нажми /start, чтобы открыть меню.",
            )
        except Exception: pass
    if released:
        try:
            await cb.message.answer(
                f"↩️ Освободил заказов: <b>{len(released)}</b> — они вернулись в очередь."
            )
        except Exception: pass

    async with session_scope() as s:
        u = await repo.get_user_by_tg(s, tg_id_int)
    if u:
        status = "🟢" if u.is_active else "🔴"
        text = (f"{status} <b>{u.full_name or '—'}</b> @{u.username or '—'}\n"
                f"{ROLE_LABELS[u.role.value]}  ·  <code>{u.tg_id}</code>")
        try: await cb.message.edit_text(text, reply_markup=_user_row_kb(u))
        except Exception: pass
    await cb.answer("✅ Сохранено")


# ---------- settings ----------
async def _settings_text() -> str:
    fin = await finished_status_ids()
    days = await sla_days()
    fdays = await fresh_days()
    nrep = await auto_status_no_repair_id()
    pm = await paid_marker()
    pm_kinds = pm.get("kind_ids") or []
    pm_subs = pm.get("name_substrings") or []
    pm_view = (
        ("kind=" + ",".join(map(str, pm_kinds)) if pm_kinds else "")
        + (("; " if pm_kinds and pm_subs else "")
           + "статус~" + ",".join(pm_subs) if pm_subs else "")
    ) or "—"
    async with session_scope() as s:
        gallery = await repo.get_setting(s, "gallery_chat_id")
    gid = (gallery or {}).get("id") or settings.gallery_chat_id or "не подключена"
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"📷 Галерея: <code>{gid}</code>\n"
        f"📅 Рабочее окно: <b>{fdays}</b> дн.\n"
        f"⏰ Простой → «застрял»: <b>{days}</b> дн.\n"
        f"✅ Закрывающие статусы: <code>{', '.join(map(str, fin)) or '—'}</code>\n"
        f"🚫 Авто-статус отказа: <code>{nrep or '—'}</code>\n"
        f"💰 Маркер «платный»: <code>{pm_view}</code>\n"
        f"🔗 RemOnline: <code>{settings.remonline_order_url}</code>\n\n"
        "<i>Чтобы подключить галерею — добавь бота в нужную группу и напиши там "
        "<code>/bind_gallery</code>.</i>"
    )


@router.callback_query(F.data == "adm:settings")
async def admin_settings(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    try: await cb.message.edit_text(await _settings_text(), reply_markup=admin_settings_kb())
    except Exception:
        await cb.message.answer(await _settings_text(), reply_markup=admin_settings_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:set:sla")
async def set_sla(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    cur = await sla_days()
    text = (
        "⏰ <b>Когда считать заказ «застрявшим»</b>\n"
        "Если заказ не двигается дольше этого срока — он попадёт в «Застряли».\n\n"
        f"Сейчас: <b>{cur}</b> дн."
    )
    try: await cb.message.edit_text(text, reply_markup=sla_days_kb(cur))
    except Exception:
        await cb.message.answer(text, reply_markup=sla_days_kb(cur))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:sla:set:"))
async def apply_sla(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    try: n = int(cb.data.rsplit(":", 1)[1])
    except Exception:
        await cb.answer("?"); return
    if n < 1 or n > 365:
        await cb.answer("вне диапазона"); return
    async with session_scope() as s:
        await repo.set_setting(s, "sla_stale_days", {"value": n})
    try: await cb.message.edit_text(await _settings_text(), reply_markup=admin_settings_kb())
    except Exception:
        await cb.message.answer(await _settings_text(), reply_markup=admin_settings_kb())
    await cb.answer(f"✅ SLA: {n} дн.")


@router.callback_query(F.data == "adm:set:fresh")
async def set_fresh(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    cur = await fresh_days()
    text = (
        "📅 <b>Рабочее окно</b>\n"
        "Сколько последних дней заказов держим на виду.\n\n"
        f"Сейчас: <b>{cur}</b> дн."
    )
    try: await cb.message.edit_text(text, reply_markup=fresh_days_kb(cur))
    except Exception:
        await cb.message.answer(text, reply_markup=fresh_days_kb(cur))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:fresh:set:"))
async def apply_fresh(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    try: n = int(cb.data.rsplit(":", 1)[1])
    except Exception:
        await cb.answer("?"); return
    if n < 1 or n > 365:
        await cb.answer("вне диапазона"); return
    await set_fresh_days(n)
    try: await cb.message.edit_text(await _settings_text(), reply_markup=admin_settings_kb())
    except Exception:
        await cb.message.answer(await _settings_text(), reply_markup=admin_settings_kb())
    await cb.answer(f"✅ Окно: {n} дн.")


@router.callback_query(F.data == "adm:set:paid")
async def set_paid_marker_menu(cb: CallbackQuery, user: User | None) -> None:
    """Configure how RemOnline orders are detected as paid (vs warranty)."""
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    pm = await paid_marker()
    kinds = pm.get("kind_ids") or []
    subs = pm.get("name_substrings") or []
    text = (
        "💰 <b>Маркер «платный»</b>\n"
        "<i>Заказ считается платным, если выполнено ЛЮБОЕ из условий:</i>\n\n"
        f"• <b>kind_of_good_id</b> ∈ <code>{', '.join(map(str, kinds)) or '—'}</code>\n"
        f"• имя статуса содержит подстроку из "
        f"<code>{', '.join(subs) or '—'}</code>\n\n"
        "<i>Пустые поля = детектор отключён, все заказы считаются гарантийными.</i>"
    )
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    b.button(text="🆔 Задать kind_of_good_id", callback_data="adm:paid:kinds")
    b.button(text="🔤 Задать подстроки статуса", callback_data="adm:paid:subs")
    b.button(text="🧹 Очистить", callback_data="adm:paid:clear")
    b.button(text="⬅️ Назад", callback_data="adm:settings")
    b.adjust(1)
    try: await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await cb.message.answer(text, reply_markup=b.as_markup())
    await cb.answer()


@router.callback_query(F.data == "adm:paid:kinds")
async def ask_paid_kinds(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    await state.set_state(AdminFSM.paid_kind_ids)
    prompt = (
        "🆔 <b>kind_of_good_id для платных заказов</b>\n"
        "Пришли список ID через запятую — например <code>12,34,56</code>.\n"
        "Пустое сообщение = очистить список. /cancel — отмена."
    )
    try: await cb.message.edit_text(prompt, reply_markup=back_kb("adm:set:paid"))
    except Exception:
        await cb.message.answer(prompt, reply_markup=back_kb("adm:set:paid"))
    await cb.answer()


@router.message(AdminFSM.paid_kind_ids)
async def on_paid_kinds(msg: Message, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user, msg.from_user.id):
        await state.clear(); return
    raw = (msg.text or "").strip()
    ids: list[int] = []
    if raw:
        for part in raw.replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ids.append(int(part))
            except ValueError:
                await msg.answer(f"Не число: <code>{part}</code>. Попробуй ещё раз или /cancel.")
                return
    await state.clear()
    await set_paid_marker(kind_ids=ids)
    await msg.answer(
        "✅ Сохранено: " + (", ".join(map(str, ids)) if ids else "пусто."),
        reply_markup=admin_settings_kb(),
    )
    await msg.answer(await _settings_text(), reply_markup=admin_settings_kb())


@router.callback_query(F.data == "adm:paid:subs")
async def ask_paid_subs(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    await state.set_state(AdminFSM.paid_name_subs)
    prompt = (
        "🔤 <b>Подстроки в статусе для платных заказов</b>\n"
        "Пришли подстроки через запятую — например <code>платн, out-of-warranty</code>.\n"
        "Регистр игнорируется. Пустое сообщение = очистить. /cancel — отмена."
    )
    try: await cb.message.edit_text(prompt, reply_markup=back_kb("adm:set:paid"))
    except Exception:
        await cb.message.answer(prompt, reply_markup=back_kb("adm:set:paid"))
    await cb.answer()


@router.message(AdminFSM.paid_name_subs)
async def on_paid_subs(msg: Message, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user, msg.from_user.id):
        await state.clear(); return
    raw = (msg.text or "").strip()
    subs: list[str] = []
    if raw:
        subs = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    await state.clear()
    await set_paid_marker(name_substrings=subs)
    await msg.answer(
        "✅ Сохранено: " + (", ".join(subs) if subs else "пусто."),
        reply_markup=admin_settings_kb(),
    )
    await msg.answer(await _settings_text(), reply_markup=admin_settings_kb())


@router.callback_query(F.data == "adm:paid:clear")
async def clear_paid_marker(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    await set_paid_marker(kind_ids=[], name_substrings=[])
    try: await cb.message.edit_text(await _settings_text(), reply_markup=admin_settings_kb())
    except Exception:
        await cb.message.answer(await _settings_text(), reply_markup=admin_settings_kb())
    await cb.answer("🧹 очищено")


@router.callback_query(F.data == "adm:set:nrep")
async def set_no_repair(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    """Pick which RO status to auto-apply on ASBIS/IT4 manager reject."""
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    loader = None
    try:
        await cb.message.edit_text("⏳ Тяну статусы из RemOnline…")
    except Exception:
        loader = await cb.message.answer("⏳ Тяну статусы из RemOnline…")
    from app.remonline.client import RemOnlineClient, RemOnlineError
    try:
        async with RemOnlineClient() as ro:
            statuses = await ro.list_order_statuses()
    except RemOnlineError as e:
        target = loader or cb.message
        await target.edit_text(f"⚠️ RemOnline вернул ошибку:\n<code>{e}</code>",
                                reply_markup=back_kb("adm:settings"))
        await cb.answer(); return
    selected = await auto_status_no_repair_id()
    text = (
        "🚫 <b>Авто-статус «Выдача без ремонта»</b>\n"
        "<i>Когда менеджер отклоняет ASBIS/IT4 — заказу автоматически "
        "выставится этот статус в RemOnline. Выбери один.</i>"
    )
    target = loader or cb.message
    try: await target.edit_text(text, reply_markup=auto_no_repair_kb(statuses, selected))
    except Exception:
        await cb.message.answer(text, reply_markup=auto_no_repair_kb(statuses, selected))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:nrep:set:"))
async def apply_no_repair(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    rid = int(cb.data.rsplit(":", 1)[1])
    await set_auto_status_no_repair_id(rid if rid else None)
    try: await cb.message.edit_text(await _settings_text(), reply_markup=admin_settings_kb())
    except Exception:
        await cb.message.answer(await _settings_text(), reply_markup=admin_settings_kb())
    await cb.answer("✅ сохранено" if rid else "✅ сброшено")


@router.callback_query(F.data == "adm:set:finished")
async def set_finished(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    loader = None
    try:
        await cb.message.edit_text("⏳ Тяну статусы из RemOnline…")
    except Exception:
        loader = await cb.message.answer("⏳ Тяну статусы из RemOnline…")
    from app.remonline.client import RemOnlineClient, RemOnlineError
    try:
        async with RemOnlineClient() as ro:
            statuses = await ro.list_order_statuses()
    except RemOnlineError as e:
        target = loader or cb.message
        await target.edit_text(f"⚠️ RemOnline вернул ошибку:\n<code>{e}</code>",
                                reply_markup=back_kb("adm:settings"))
        await cb.answer(); return
    selected = set(await finished_status_ids())
    text = (
        "✅ <b>Закрывающие статусы</b>\n"
        "<i>Отмеченные статусы убирают заказ из очереди и списков — как «готово».\n"
        "Тапни, чтобы включить или выключить. Изменения применяются сразу.</i>"
    )
    target = loader or cb.message
    try: await target.edit_text(text, reply_markup=finished_toggle_kb(statuses, selected))
    except Exception:
        await cb.message.answer(text, reply_markup=finished_toggle_kb(statuses, selected))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:fin:tg:"))
async def toggle_finished(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    rid = int(cb.data.split(":")[3])
    current = set(await finished_status_ids())
    if rid in current:
        current.discard(rid)
    else:
        current.add(rid)
    await set_finished_status_ids(sorted(current))
    from app.remonline.client import RemOnlineClient
    try:
        async with RemOnlineClient() as ro:
            statuses = await ro.list_order_statuses()
    except Exception:
        statuses = []
    if statuses:
        try: await cb.message.edit_reply_markup(reply_markup=finished_toggle_kb(statuses, current))
        except Exception: pass
    await cb.answer("включён" if rid in current else "выключен")


# ---------- diagnostics ----------
@router.callback_query(F.data == "adm:diag")
async def admin_diag(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    from app.remonline.client import RemOnlineClient, RemOnlineError
    try: await cb.message.edit_text("⏳ Проверяю, всё ли работает…")
    except Exception: pass
    status = "🟢 на связи"; detail = ""
    try:
        async with RemOnlineClient() as ro:
            data = await ro.list_orders(page=1)
            detail = f"заказов на первой странице: {len((data or {}).get('data') or [])}"
    except RemOnlineError as e:
        status = "🔴 ошибка"; detail = str(e)[:300]
    except Exception as e:
        status = "🔴 ошибка"; detail = f"{type(e).__name__}: {e}"[:300]
    async with session_scope() as s:
        queue_n = await repo.count_queue(s)
        users_n = len(await repo.list_users(s))
        pending = len(await repo.pending_events(s, limit=200))
        gallery = await repo.get_setting(s, "gallery_chat_id")
    gallery_line = (gallery or {}).get("id") or settings.gallery_chat_id or "не подключена"
    text = (
        "🩺 <b>Диагностика</b>\n\n"
        f"RemOnline: {status}\n<i>{detail}</i>\n\n"
        f"Галерея: <code>{gallery_line}</code>\n"
        f"В очереди: <b>{queue_n}</b>\n"
        f"Пользователей: <b>{users_n}</b>\n"
        f"Событий ждут отправки: <b>{pending}</b>"
    )
    try: await cb.message.edit_text(text, reply_markup=back_kb())
    except Exception: await cb.message.answer(text, reply_markup=back_kb())
    await cb.answer()


# ---------- actions ----------
@router.callback_query(F.data == "adm:act:flush")
async def act_flush(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    from app.bot.services.feed import flush_events
    try: await cb.message.edit_text("⏳ Обновляю виджеты…")
    except Exception: pass
    n = await flush_events(bot, limit=200)
    msg_out = (
        f"📤 Опубликовано событий: <b>{n}</b>" if n
        else "📭 Новых событий нет."
    )
    try: await cb.message.edit_text(msg_out, reply_markup=back_kb())
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data == "adm:act:widgets")
async def act_widgets_rebuild(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    """Force-rebuild group widgets (stats/queue/requests) right now.

    Drops the stored message IDs so new dashboards get created as fresh
    pinned messages instead of being edited in place.
    """
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    from app.bot.services.gallery import resolve_gallery_chat_id
    from app.bot.services.widgets import refresh_dashboard
    chat_id = await resolve_gallery_chat_id()
    if not chat_id:
        try: await cb.message.edit_text(
            "ℹ️ Галерея ещё не подключена.\n"
            "Добавь бота в нужную группу и напиши там <code>/bind_gallery</code>.",
            reply_markup=back_kb())
        except Exception: pass
        await cb.answer(); return
    async with session_scope() as s:
        old = await repo.get_setting(s, "widget_mids") or {}
        await repo.set_setting(s, "widget_mids", {})
    for mid in (old or {}).values():
        try: await bot.delete_message(chat_id, int(mid))
        except Exception: pass
    await refresh_dashboard(bot)
    try: await cb.message.edit_text(
        "🔄 <b>Виджеты пересобраны</b>\n"
        "В группе появятся три закреплённых дашборда: сводка, очередь, запросы.",
        reply_markup=back_kb())
    except Exception: pass
    await cb.answer("Готово")


@router.callback_query(F.data == "adm:act:wipeN")
async def act_wipe_n_ask(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    """Ask how many latest messages to hard-delete from the gallery group."""
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    from app.bot.services.gallery import resolve_gallery_chat_id
    chat_id = await resolve_gallery_chat_id()
    if not chat_id:
        try: await cb.message.edit_text(
            "ℹ️ Галерея ещё не подключена.\n"
            "Добавь бота в группу и вызови там <code>/bind_gallery</code>.",
            reply_markup=back_kb())
        except Exception: pass
        await cb.answer(); return
    await state.set_state(AdminFSM.wipe_count)
    await state.update_data(chat_id=chat_id)
    try: await cb.message.edit_text(
        "🧹 <b>Чистка галереи</b>\n"
        "Сколько последних сообщений удалить? Можно до 5000.\n\n"
        "<i>Бот должен быть администратором группы с правом «удалять сообщения». "
        "Что не удалится — пропущу.</i>\n\n"
        "Напиши число или /cancel.",
        reply_markup=back_kb())
    except Exception: pass
    await cb.answer()


@router.message(AdminFSM.wipe_count)
async def act_wipe_n_go(msg: Message, user: User | None, bot: Bot, state: FSMContext) -> None:
    if not _is_admin(user, msg.from_user.id if msg.from_user else None):
        await state.clear(); return
    text = (msg.text or "").strip()
    if text.lower() in ("/cancel", "отмена", "cancel"):
        await state.clear()
        await msg.answer("⬅️ Отменил."); return
    try:
        n = int(text)
    except Exception:
        await msg.answer("Нужно просто число, например <code>500</code>.")
        return
    n = max(1, min(n, 5000))
    data = await state.get_data()
    chat_id = int(data.get("chat_id"))
    await state.clear()

    try:
        anchor = await bot.send_message(chat_id, "⏳", disable_notification=True)
    except Exception:
        await msg.answer("⛔ Не получилось написать в группу. Проверь, что бот там админ.")
        return
    top = anchor.message_id
    status = await msg.answer(f"🧹 Чищу последние <b>{n}</b> сообщений…")
    ok, fail = 0, 0
    deleted_ids: list[int] = []
    for i in range(n):
        mid = top - i
        if mid <= 0: break
        try:
            await bot.delete_message(chat_id, mid)
            ok += 1
            deleted_ids.append(mid)
        except Exception:
            fail += 1
        if i % 50 == 0 and i > 0:
            try: await status.edit_text(f"🧹 удалено: {ok} · осталось: {n - i}")
            except Exception: pass
        await asyncio.sleep(0.03)
    if deleted_ids:
        async with session_scope() as s:
            await repo.clear_gallery_tracking(s, deleted_ids)
    try: await status.edit_text(
        f"✅ <b>Галерея почищена</b>\n"
        f"Удалено: <b>{ok}</b>\n\n"
        f"<i>Теперь нажми «🔄 Пересобрать виджеты» — дашборды соберутся заново.</i>")
    except Exception: pass


@router.callback_query(F.data == "adm:act:galclear")
async def act_galclear(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    from app.bot.services.gallery import resolve_gallery_chat_id
    chat_id = await resolve_gallery_chat_id()
    if not chat_id:
        try: await cb.message.edit_text(
            "ℹ️ Галерея не подключена.\n"
            "Добавь бота в группу и напиши там <code>/bind_gallery</code>.",
            reply_markup=back_kb())
        except Exception: pass
        await cb.answer(); return
    async with session_scope() as s:
        c = await repo.gallery_counts(s)
    total = int(c.get("total") or 0)
    if total == 0:
        try: await cb.message.edit_text(
            "🧹 <b>В памяти пусто</b>\n"
            "Ни одного сообщения этого бота не сохранено — удалять через базу нечего.\n\n"
            "<i>Если в группе остались старые сообщения, используй «🧹 Чистка галереи» — "
            "бот пройдёт по последним N сообщениям и удалит их жёстко.</i>",
            reply_markup=back_kb())
        except Exception: pass
        await cb.answer(); return
    txt = (
        f"🧹 <b>Очистка галереи</b>\n\n"
        f"Собираюсь удалить <b>{total}</b> сообщений этого бота:\n"
        f"• события ленты — {c.get('events', 0)}\n"
        f"• фото и документы — {c.get('photos', 0)}\n\n"
        f"<i>Чужие сообщения (другие боты, люди) не трогаю.</i>\n\n"
        f"Продолжить?"
    )
    try: await cb.message.edit_text(txt, reply_markup=confirm_kb("adm:act:galclear:go"))
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data == "adm:act:galclear:go")
async def act_galclear_go(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    from app.bot.services.gallery import resolve_gallery_chat_id
    chat_id = await resolve_gallery_chat_id()
    if not chat_id:
        await cb.answer("Нет группы-галереи", show_alert=True); return
    async with session_scope() as s:
        ids = await repo.gallery_tracked_message_ids(s)
    try: await cb.message.edit_text(f"⏳ Удаляю {len(ids)} сообщений…")
    except Exception: pass
    ok, fail = 0, 0
    deleted_ids: list[int] = []
    for mid in ids:
        try:
            await bot.delete_message(chat_id, mid)
            ok += 1
            deleted_ids.append(mid)
        except Exception:
            fail += 1
        await asyncio.sleep(0.03)
    if deleted_ids:
        async with session_scope() as s:
            await repo.clear_gallery_tracking(s, deleted_ids)
    out = (
        f"✅ <b>Готово</b>\n"
        f"Удалено: <b>{ok}</b>" +
        (f"\n<i>Пропущено: {fail} — слишком старые или нет прав.</i>" if fail else "")
    )
    try: await cb.message.edit_text(out, reply_markup=back_kb())
    except Exception: pass
    await cb.answer("Готово")


@router.callback_query(F.data == "adm:act:seed")
async def act_seed(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    finished = await finished_status_ids()
    fdays = await fresh_days()
    async with session_scope() as s:
        n = await repo.seed_queue_from_orders(s, exclude_status_ids=finished, fresh_days=fdays)
    msg_out = (
        f"🌱 Добавил в очередь: <b>{n}</b>"
        if n else "🌱 В очередь нечего добавлять — все заказы уже там."
    )
    try: await cb.message.edit_text(msg_out, reply_markup=back_kb())
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data == "adm:act:reindex")
async def act_reindex(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    async with session_scope() as s:
        n = await repo.reindex_queue(s)
    try: await cb.message.edit_text(
        f"🔁 Перенумеровал очередь. Позиций: <b>{n}</b>",
        reply_markup=back_kb())
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data == "adm:act:rostatuses")
async def act_rostatuses(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    try: await cb.message.edit_text("⏳ Тяну статусы из RemOnline…")
    except Exception: pass
    from app.remonline.client import RemOnlineClient, RemOnlineError
    try:
        async with RemOnlineClient() as ro:
            rows = await ro.list_order_statuses()
    except RemOnlineError as e:
        try: await cb.message.edit_text(f"⚠️ Ошибка:\n<code>{e}</code>", reply_markup=back_kb())
        except Exception: pass
        await cb.answer(); return
    lines = [f"🏷 <b>Статусы RemOnline</b> · всего {len(rows)}",
             "<i>id — название · группа</i>"]
    for r in rows[:80]:
        lines.append(f"• <code>{r.get('id')}</code> — {r.get('name')} · g{r.get('group')}")
    if len(rows) > 80:
        lines.append(f"<i>…и ещё {len(rows)-80}</i>")
    try: await cb.message.edit_text("\n".join(lines), reply_markup=back_kb())
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data == "adm:act:broadcast")
async def act_broadcast(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    await state.set_state(AdminFSM.broadcast)
    await cb.message.answer(
        "📢 <b>Рассылка</b>\n"
        "Пришли текст одним сообщением — отправлю всем активным пользователям.\n"
        "Поддерживается форматирование (<b>жирный</b>, <i>курсив</i>, <code>код</code>).\n\n"
        "/cancel — отмена."
    )
    await cb.answer()


@router.message(AdminFSM.broadcast)
async def on_broadcast_text(msg: Message, user: User | None, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(user, msg.from_user.id): return
    await state.clear()
    text = (msg.text or "").strip()
    if not text:
        await msg.answer("Пустой текст — рассылка отменена.")
        return
    async with session_scope() as s:
        users = await repo.list_users(s)
    loader = await msg.answer("⏳ Отправляю…")
    ok = fail = 0
    for i, u in enumerate(users):
        if not u.is_active: continue
        try:
            await bot.send_message(u.tg_id, f"📢 <b>Объявление</b>\n{text}")
            ok += 1
        except Exception:
            fail += 1
        if (i + 1) % 25 == 0:
            await asyncio.sleep(1.0)
    tail = f"\n<i>не доставлено: {fail}</i>" if fail else ""
    try: await loader.edit_text(
        f"📢 Доставлено: <b>{ok}</b>{tail}",
        reply_markup=back_kb(),
    )
    except Exception: pass


# ---------- invite ----------
@router.callback_query(F.data == "adm:invite")
async def cb_invite(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    text = (
        "➕ <b>Пригласить пользователя</b>\n\n"
        "<b>🔗 Ссылка-приглашение</b> — одноразовая, с уже выбранной ролью. "
        "Отправляешь ссылку, человек открывает — роль выдаётся автоматически.\n\n"
        "<b>🆔 По Telegram ID</b> — если знаешь ID, выдаём роль сразу."
    )
    try: await cb.message.edit_text(text, reply_markup=invite_menu_kb())
    except Exception:
        await cb.message.answer(text, reply_markup=invite_menu_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:inv:link")
async def cb_invite_link(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    text = "🔗 <b>Ссылка-приглашение</b>\nКакую роль получит приглашённый?"
    try: await cb.message.edit_text(text, reply_markup=invite_role_pick_kb("link"))
    except Exception:
        await cb.message.answer(text, reply_markup=invite_role_pick_kb("link"))
    await cb.answer()


@router.callback_query(F.data == "adm:inv:byid")
async def cb_invite_byid(cb: CallbackQuery, user: User | None) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    text = "🆔 <b>Добавить по Telegram ID</b>\nКакую роль выдать?"
    try: await cb.message.edit_text(text, reply_markup=invite_role_pick_kb("byid"))
    except Exception:
        await cb.message.answer(text, reply_markup=invite_role_pick_kb("byid"))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:inv:role:"))
async def cb_invite_role(cb: CallbackQuery, user: User | None,
                         state: FSMContext, bot: Bot) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    _, _, _, mode, role_val = cb.data.split(":")
    try: role = Role(role_val)
    except Exception:
        await cb.answer("Неизвестная роль"); return

    if mode == "link":
        token = await create_invite(role, created_by=cb.from_user.id)
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start=inv_{token}"
        text = (
            f"🔗 <b>Ссылка готова</b>\n"
            f"Роль: <b>{ROLE_LABELS[role.value]}</b>\n"
            f"Действует 72 часа, срабатывает один раз.\n\n"
            f"<code>{link}</code>"
        )
        try: await cb.message.edit_text(text, reply_markup=back_kb("adm:invite"))
        except Exception:
            await cb.message.answer(text, reply_markup=back_kb("adm:invite"))
        await cb.answer("✅ Ссылка готова")
        return

    # mode == "byid"
    await state.set_state(AdminFSM.invite_tg_id)
    await state.update_data(role=role.value)
    text = (
        f"🆔 <b>Добавить по ID</b>\n"
        f"Пришли Telegram ID — выдам роль <b>{ROLE_LABELS[role.value]}</b>.\n"
        f"<i>ID можно узнать у @userinfobot или попросив человека написать боту /myid.</i>\n\n"
        f"/cancel — отмена."
    )
    try: await cb.message.edit_text(text)
    except Exception:
        await cb.message.answer(text)
    await cb.answer()


@router.message(AdminFSM.invite_tg_id)
async def on_invite_tg_id(msg: Message, user: User | None, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(user, msg.from_user.id if msg.from_user else None):
        await state.clear(); return
    data = await state.get_data()
    await state.clear()
    try: role = Role(data.get("role"))
    except Exception:
        await msg.answer("⚠️ Потерял контекст — начни заново."); return
    raw = (msg.text or "").strip().lstrip("@")
    try: tg_id = int(raw)
    except Exception:
        await msg.answer("Пришли число — это Telegram ID. /cancel — отмена.",
                         reply_markup=invite_menu_kb()); return
    async with session_scope() as s:
        await repo.upsert_user(s, tg_id, role)
    note_dm = ""
    try:
        await bot.send_message(
            tg_id,
            f"🔔 Тебе выдана роль: <b>{ROLE_LABELS[role.value]}</b>.\n"
            f"Нажми /start, чтобы открыть меню.",
        )
        note_dm = "\n<i>Уведомление ушло пользователю.</i>"
    except Exception:
        note_dm = (
            "\n<i>Не получилось написать ему первым — попроси сначала "
            "открыть бота командой /start.</i>"
        )
    await msg.answer(
        f"✅ <code>{tg_id}</code> добавлен как <b>{ROLE_LABELS[role.value]}</b>.{note_dm}",
        reply_markup=back_kb("adm:invite"),
    )


# ---------- role assignment (admin-panel only) ----------


# ---------- legacy commands (оставляем как дубли) ----------
@router.message(Command("role"))
async def cmd_role(msg: Message, user: User | None) -> None:
    if not _is_admin(user, msg.from_user.id): return
    parts = (msg.text or "").split()
    if len(parts) != 3:
        await msg.answer("Формат: <code>/role &lt;tg_id&gt; &lt;admin|reception|engineer|manager&gt;</code>\n"
                         "Удобнее — через 🛡 Админ → 👥 Пользователи."); return
    try: tg_id = int(parts[1]); role = Role(parts[2])
    except Exception:
        await msg.answer("Неверные аргументы."); return
    async with session_scope() as s:
        await repo.upsert_user(s, tg_id, role)
    await msg.answer(f"✅ {tg_id} → {ROLE_LABELS[role.value]}")


@router.callback_query(F.data.startswith("adm:role:"))
async def cb_role_from_request(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    _, _, tg_id, role_val = cb.data.split(":")
    tg_id_int = int(tg_id)
    if role_val == "deny":
        async with session_scope() as s:
            await repo.set_user_active(s, tg_id_int, False)
        try: await cb.message.edit_text(f"🚫 Заявка от <code>{tg_id_int}</code> отклонена.")
        except Exception: pass
        await cb.answer(); return
    try: role = Role(role_val)
    except Exception:
        await cb.answer("Неизвестная роль"); return
    async with session_scope() as s:
        await repo.upsert_user(s, tg_id_int, role)
    try: await bot.send_message(
        tg_id_int,
        f"🔔 Тебе выдана роль: <b>{ROLE_LABELS[role.value]}</b>.\n"
        f"Нажми /start, чтобы открыть меню."
    )
    except Exception: pass
    try: await cb.message.edit_text(
        f"✅ <code>{tg_id_int}</code> — роль: <b>{ROLE_LABELS[role.value]}</b>"
    )
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data.startswith("adm:q:"))
async def cb_queue_move(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    _, _, action, order_id = cb.data.split(":")
    oid = int(order_id)
    async with session_scope() as s:
        if action == "up":
            await repo.move_queue(s, oid, -1)
        elif action == "dn":
            await repo.move_queue(s, oid, +1)
        elif action == "top":
            await repo.move_queue_top(s, oid)
        order = await repo.get_order(s, oid)
        pos_row = (await s.execute(
            select(QueueItem.position).where(QueueItem.order_id == oid)
        )).scalar_one_or_none()
        await repo.add_event(s, "queue_moved", order_id=oid,
                             payload={"number": order.number if order else oid, "pos": pos_row or 0})
    actor = user.full_name or (user.username and f"@{user.username}") or str(user.tg_id)
    label = {"up": "приоритет ↑", "dn": "приоритет ↓", "top": "🔝 в топ очереди"}.get(action, action)
    from app.bot.services.sync_ro import push_action_comment
    await push_action_comment(oid, f"Изменён {label}", actor=f"{actor} · 👑 админ",
                              detail=f"новая позиция: {pos_row or '—'}")
    # Refresh whichever widget the admin is viewing (queue list or order card).
    from app.bot import views
    base = (getattr(cb.message, "html_text", None) or cb.message.text or "")
    try:
        if base.startswith("📋 <b>Очередь → #"):
            out = await views.render_queue_order_view(user, oid, 0, bot)
            if out:
                text, kb = out
                await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        elif base.startswith("📋 <b>Очередь</b>"):
            text, kb = await views.render_queue_view(user, page=0)
            await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await cb.answer("🔝 Позиция обновлена")


# --------------------------- assign / transfer engineer ---------------------------

@router.callback_query(F.data.startswith("adm:as:"))
async def cb_assign_picker(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    """Open the engineer-picker for the given order."""
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    _, _, oid_s, page_s = cb.data.split(":")
    oid, page = int(oid_s), int(page_s)
    from app.bot import views
    out = await views.render_assign_picker(user, oid, page)
    if not out:
        await cb.answer("Заказ не найден", show_alert=True); return
    text, kb = out
    try: await cb.message.edit_text(text, reply_markup=kb)
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data.startswith("adm:un:"))
async def cb_unassign(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    _, _, oid_s = cb.data.split(":")
    oid = int(oid_s)
    async with session_scope() as s:
        status, prev = await repo.force_assign_order(s, oid, None)
        order = await repo.get_order(s, oid)
        if status == "ok":
            await repo.add_event(s, "order_unassigned", order_id=oid, payload={
                "number": order.number if order else oid,
                "prev_engineer_id": prev,
                "by": user.full_name or str(user.tg_id),
            })
    if status != "ok":
        await cb.answer("Уже без исполнителя"); return
    actor = user.full_name or (user.username and f"@{user.username}") or str(user.tg_id)
    prev_name = await _resolve_user_name(prev) if prev else "—"
    from app.bot.services.sync_ro import push_action_comment
    await push_action_comment(oid, "Снят инженер", actor=f"{actor} · 👑 админ",
                              detail=f"был: {prev_name}")
    if prev:
        await _notify_engineer(bot, prev, oid, kind="unassigned", by=actor)
    from app.bot import views
    out = await views.render_queue_order_view(user, oid, 0, bot)
    if out:
        text, kb = out
        try: await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except Exception: pass
    await cb.answer("↩️ Снят с инженера")


@router.callback_query(F.data.startswith("adm:asgo:"))
async def cb_assign_apply(cb: CallbackQuery, user: User | None, bot: Bot) -> None:
    """Apply assignment: order N → engineer E."""
    if not _is_admin(user, cb.from_user.id):
        await cb.answer("🔒"); return
    _, _, oid_s, eid_s = cb.data.split(":")
    oid, eid = int(oid_s), int(eid_s)
    new_name = await _resolve_user_name(eid)
    if not new_name:
        await cb.answer("Инженер не найден", show_alert=True); return
    async with session_scope() as s:
        status, prev = await repo.force_assign_order(s, oid, eid)
        order = await repo.get_order(s, oid)
        if status == "ok":
            await repo.add_event(s, "order_assigned", order_id=oid, payload={
                "number": order.number if order else oid,
                "engineer_id": eid,
                "engineer": new_name,
                "prev_engineer_id": prev,
                "by": user.full_name or str(user.tg_id),
            })
    if status == "noop":
        await cb.answer("Уже на этом инженере"); return
    if status == "not_found":
        await cb.answer("Заказ не найден", show_alert=True); return
    actor = user.full_name or (user.username and f"@{user.username}") or str(user.tg_id)
    prev_name = await _resolve_user_name(prev) if prev else None
    from app.bot.services.sync_ro import push_action_comment
    detail = f"назначен: {new_name}" + (f" (был: {prev_name})" if prev_name else "")
    await push_action_comment(oid, "Передан инженеру", actor=f"{actor} · 👑 админ", detail=detail)
    await _notify_engineer(bot, eid, oid, kind="assigned", by=actor)
    if prev and prev != eid:
        await _notify_engineer(bot, prev, oid, kind="reassigned", by=actor, to=new_name)
    from app.bot import views
    out = await views.render_queue_order_view(user, oid, 0, bot)
    if out:
        text, kb = out
        try: await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except Exception: pass
    await cb.answer(f"✅ → {new_name}")


async def _resolve_user_name(uid: int | None) -> str | None:
    if not uid:
        return None
    from app.db.models import User as UM
    async with session_scope() as s:
        u = (await s.execute(select(UM).where(UM.id == uid))).scalar_one_or_none()
    if not u:
        return None
    return u.full_name or (u.username and f"@{u.username}") or str(u.tg_id)


async def _notify_engineer(bot: Bot, engineer_id: int, order_id: int, *,
                            kind: str, by: str, to: str | None = None) -> None:
    """Event-driven DM: «менеджер передал заказ — можно начинать»."""
    from app.db.models import User as UM
    async with session_scope() as s:
        u = (await s.execute(select(UM).where(UM.id == engineer_id))).scalar_one_or_none()
        order = await repo.get_order(s, order_id)
    if not u or not order:
        return
    dl = await _safe_deep_link(bot, order_id)
    num = order.number or order_id
    if kind == "assigned":
        text = (
            f"📬 <b>На тебя назначили заказ</b>\n"
            f"📦 <b>#{num}</b>"
            + (f" · {order.device}" if order.device else "")
            + f"\nНазначил: {by}\n\n"
            f"<a href='{dl}'>Открыть в боте</a>"
        )
    elif kind == "reassigned":
        text = (
            f"🔄 <b>Заказ #{num} передан другому</b>\n"
            f"Новый исполнитель: {to or '—'}\n"
            f"Передал: {by}"
        )
    elif kind == "unassigned":
        text = (
            f"↩️ <b>Заказ #{num} снят с тебя</b>\n"
            f"Возвращён в очередь админом: {by}"
        )
    else:
        return
    try:
        await bot.send_message(u.tg_id, text, disable_web_page_preview=True)
    except Exception:
        pass


async def _safe_deep_link(bot: Bot, order_id: int) -> str:
    try:
        from app.bot.services.deep_link import order_deep_link
        return await order_deep_link(bot, order_id)
    except Exception:
        return ""
