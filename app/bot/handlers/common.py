from __future__ import annotations
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.config import settings
from app.db.session import session_scope
from app.db import repo
from app.db.models import Role, User
from app.bot.texts import (
    HELLO, NO_ACCESS, ACCESS_REQUESTED, ROLE_LABELS, ROLE_ICONS, ROLE_TIPS, CANCELLED,
)
from app.bot.keyboards import main_menu, role_pick_kb, order_card_kb
from app.bot.commands import apply_commands_for_user
from app.bot.services.deep_link import order_deep_link
from app.bot.services.ro_url import resolve_ro_url
from app.bot.services.invites import consume_invite
from app.logger import log

router = Router(name="common")


class OrderFSM(StatesGroup):
    waiting_number = State()


def _caps_for(user: User | None) -> dict:
    """Capabilities bundle for order card based on role."""
    if not user:
        return {}
    is_admin  = user.role == Role.admin
    is_rec    = user.role == Role.reception
    is_eng    = user.role == Role.engineer
    is_mgr    = user.role == Role.manager
    return {
        "is_admin": is_admin,
        "for_reception": is_rec or is_admin,
        "can_take":    is_eng or is_admin,
        "can_request": is_eng or is_mgr or is_admin,
    }


async def _fmt_order_card(o) -> str:
    from app.bot.texts import status_emoji
    async with session_scope() as s:
        n = await repo.photos_count(s, o.id)
    head = f"📦 <b>#{o.number}</b> · фото: <b>{n}</b>"
    if o.status_name:
        head += f"  ·  {status_emoji(o.status_name)} {o.status_name}"
    lines = [head]
    if o.device:      lines.append(o.device)
    if o.serial:      lines.append(f"🔢 {o.serial}")
    if o.client_name: lines.append(f"👤 {o.client_name}")
    if o.last_activity_at:
        lines.append(f"🕘 {o.last_activity_at:%d.%m %H:%M}")
    return "\n".join(lines)


async def _send_order_card(msg: Message, user: User, order, bot: Bot) -> None:
    caps = _caps_for(user)
    taken = bool(user and order.assigned_engineer_id == user.id)
    ro_url = await resolve_ro_url(order)
    dl = await order_deep_link(bot, order.id)
    kb = order_card_kb(
        order.id,
        taken=taken,
        bot_deep_link=dl,
        ro_url=ro_url,
        **caps,
    )
    await msg.answer(await _fmt_order_card(order), reply_markup=kb)


# ===== /start & deep links =====
@router.message(CommandStart(deep_link=True))
async def on_start_deep(msg: Message, command: CommandObject, user: User | None, bot: Bot) -> None:
    payload = command.args or ""
    if payload.startswith("inv_"):
        token = payload.split("_", 1)[1]
        tg = msg.from_user
        if tg is None:
            return
        role = await consume_invite(token)
        if role is None:
            await msg.answer(
                "🔒 Ссылка недействительна или уже использована.\n"
                "Попроси админа прислать новую."
            ); return
        async with session_scope() as s:
            await repo.upsert_user(s, tg.id, role,
                                   username=tg.username, full_name=tg.full_name)
        await apply_commands_for_user(bot, tg.id, role)
        icon = ROLE_ICONS.get(role.value, "")
        label = ROLE_LABELS[role.value]
        tips = ROLE_TIPS.get(role.value, "")
        await msg.answer(
            f"{HELLO}\n\n"
            f"✅ Добро пожаловать!\n"
            f"Твоя роль: <b>{icon} {label}</b>\n\n"
            f"{tips}\n\n"
            f"<i>Всё управление внизу — кнопками.</i>",
            reply_markup=main_menu(role),
        )
        async with session_scope() as s:
            admins = await repo.list_users(s, role=Role.admin)
        admin_ids = {a.tg_id for a in admins if a.is_active} | set(settings.admin_ids)
        note = (
            f"➕ <b>Новый пользователь</b>\n"
            f"{tg.full_name} @{tg.username or '—'} · <code>{tg.id}</code>\n"
            f"Роль: {icon} {label}"
        )
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, note)
            except Exception:
                pass
        return
    if payload.startswith("order_"):
        try:
            oid = int(payload.split("_", 1)[1])
        except ValueError:
            oid = None
        if oid is not None:
            async with session_scope() as s:
                order = await repo.get_order(s, oid)
            if order and user:
                await _send_order_card(msg, user, order, bot)
                return
    await on_start(msg, user, bot)


@router.message(CommandStart())
async def on_start(msg: Message, user: User | None, bot: Bot) -> None:
    tg = msg.from_user
    if tg is None:
        return

    if user is None:
        promote_admin = tg.id in settings.admin_ids
        if not promote_admin:
            async with session_scope() as s:
                existing = await repo.list_users(s, role=Role.admin)
            if not existing:
                promote_admin = True
        if promote_admin:
            async with session_scope() as s:
                user = await repo.upsert_user(
                    s, tg.id, Role.admin,
                    username=tg.username, full_name=tg.full_name,
                )
            await apply_commands_for_user(bot, tg.id, Role.admin)

    if user is None:
        await msg.answer(f"{HELLO}\n\n{NO_ACCESS}\n\n{ACCESS_REQUESTED}")
        text = (
            f"✉️ <b>Запрос доступа</b>\n"
            f"{tg.full_name} @{tg.username or '—'}\n"
            f"<code>id={tg.id}</code>\n\n"
            f"Выбери роль или отклони."
        )
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, text, reply_markup=role_pick_kb(tg.id))
            except Exception:
                pass
        return

    if not user.is_active:
        await msg.answer(NO_ACCESS); return

    if user.username != tg.username or user.full_name != tg.full_name:
        async with session_scope() as s:
            await repo.upsert_user(s, tg.id, user.role,
                                   username=tg.username, full_name=tg.full_name)

    await apply_commands_for_user(bot, tg.id, user.role)
    icon  = ROLE_ICONS.get(user.role.value, "")
    label = ROLE_LABELS[user.role.value]
    tips  = ROLE_TIPS.get(user.role.value, "")
    await msg.answer(
        f"{HELLO}\n\n"
        f"Твоя роль: <b>{icon} {label}</b>\n\n"
        f"{tips}",
        reply_markup=main_menu(user.role),
    )


# ===== simple info =====
@router.message(Command("whoami"))
async def whoami(msg: Message, user: User | None) -> None:
    tg = msg.from_user
    if user:
        icon  = ROLE_ICONS.get(user.role.value, "")
        label = ROLE_LABELS[user.role.value]
        real  = getattr(user, "real_role", None)
        lines = [
            f"🆔 <code>{tg.id}</code>",
            f"Роль: <b>{icon} {label}</b>",
        ]
        if real and real != user.role:
            lines.append(
                f"<i>сейчас в режиме проверки; реальная роль — "
                f"{ROLE_ICONS.get(real.value,'')} {ROLE_LABELS[real.value]}</i>"
            )
        lines.append("🟢 активен" if user.is_active else "🔴 отключён")
        if user.ro_employee_id:
            lines.append(f"RemOnline id: <code>{user.ro_employee_id}</code>")
        await msg.answer("\n".join(lines))
    else:
        await msg.answer(f"🆔 <code>{tg.id}</code>\n🔒 доступ ещё не выдан")


@router.message(Command("myid"))
async def myid(msg: Message) -> None:
    tg = msg.from_user
    await msg.answer(f"Твой Telegram ID:\n<code>{tg.id}</code>")


# ===== stats (all roles) =====
@router.message(Command("stats"))
@router.message(StateFilter(None), F.text.in_({"📊 Статистика", "Статистика"}))
async def on_stats(msg: Message, user: User | None, bot: Bot) -> None:
    if not user or not user.is_active:
        return
    try: await bot.send_chat_action(msg.chat.id, "typing")
    except Exception: pass
    from app.bot.services.stats import render_stats
    try:
        text = await render_stats(user)
    except Exception as e:
        log.exception("stats_render_failed", error=str(e))
        text = "⚠️ Не смог собрать статистику. Попробуй ещё раз через минуту."
    await msg.answer(text)


# ===== /cancel (universal) =====
@router.message(Command("cancel"))
async def cancel(msg: Message, state: FSMContext) -> None:
    cur = await state.get_state()
    if cur:
        await state.clear()
        await msg.answer(CANCELLED)
    else:
        await msg.answer("Сейчас нечего отменять.")


# ===== universal «🔎 Заказ» / /order =====
_ASK_NUMBER = (
    "🔎 Пришли <b>номер заказа</b> одним сообщением — например <code>12345</code>.\n"
    "/cancel — отмена."
)


@router.message(StateFilter(None), F.text.in_({"🔎 Заказ", "Заказ"}))
async def btn_find_order(msg: Message, user: User | None, state: FSMContext) -> None:
    if not user or not user.is_active:
        return
    await state.set_state(OrderFSM.waiting_number)
    await msg.answer(_ASK_NUMBER)


@router.message(Command("order"))
async def cmd_order(msg: Message, user: User | None, bot: Bot, state: FSMContext) -> None:
    if not user or not user.is_active:
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await state.set_state(OrderFSM.waiting_number)
        await msg.answer(_ASK_NUMBER)
        return
    await _lookup_and_show(msg, user, bot, parts[1])


@router.message(OrderFSM.waiting_number)
async def on_order_number(msg: Message, user: User | None, bot: Bot, state: FSMContext) -> None:
    if not user or not user.is_active:
        await state.clear(); return
    raw = (msg.text or "").strip()
    if not raw:
        await msg.answer("Пустой номер. Пришли номер заказа или /cancel.")
        return
    await state.clear()
    await _lookup_and_show(msg, user, bot, raw)


async def _lookup_and_show(msg: Message, user: User, bot: Bot, raw: str) -> None:
    number = raw.strip().lstrip("#")
    async with session_scope() as s:
        o = await repo.get_order_by_number(s, number)
    if not o:
        await msg.answer(
            f"Не нашёл заказ <code>#{number}</code>.\n"
            "Проверь номер или подожди минуту — он синхронизируется с RemOnline."
        )
        return
    await _send_order_card(msg, user, o, bot)
