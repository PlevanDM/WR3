"""Gallery-group only commands.

The gallery chat has a dedicated toolkit that lives *inside* the group (no DM
switch required). Two tiers:

**Public** (any member)
* `/find <номер>`    — ссылки на фото конкретного заказа
* `/stats`           — краткая сводка (то же что в топ-виджете)
* `/gallery_info`    — chat_id и id закреплённых виджетов

**Admin** (config via `admin_ids` or DB role=admin)
* `/bind_gallery [N]`  — закрепить эту группу как галерею + (опц.) очистить N сообщений
* `/unbind_gallery`    — снять привязку
* `/rebuild`           — пересоздать 3 пинованых виджета (stats/queue/requests)
* `/wipe N`            — удалить последние N сообщений (≤5000)

All user-facing bot UI (queue, mine, inbox, etc.) is DM-only and blocked at
router level by a `F.chat.type == "private"` filter in `handlers/__init__.py`.
"""
from __future__ import annotations
import asyncio
from aiogram import Router, Bot, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db import repo
from app.db.models import User, Role
from app.bot.services.gallery import (
    save_gallery_chat_id, resolve_gallery_chat_id, gallery_photo_link,
)
from app.bot.services.widgets import refresh_dashboard
from app.bot.services.stats import render_stats
from app.bot.commands import apply_gallery_commands, clear_gallery_commands
from app.logger import log

router = Router(name="group")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


# ---------- helpers ----------
async def _is_admin(tg_id: int | None) -> bool:
    if tg_id is None:
        return False
    if tg_id in settings.admin_ids:
        return True
    async with session_scope() as s:
        u = (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    return bool(u and u.role == Role.admin and u.is_active)


async def _is_in_gallery(chat_id: int) -> bool:
    gid = await resolve_gallery_chat_id()
    return gid == chat_id


async def _bulk_delete(bot: Bot, chat_id: int, top_mid: int, count: int) -> tuple[int, int]:
    """Bulk-delete up to `count` messages going backwards from `top_mid`.
    Uses deleteMessages in chunks of 100; falls back to per-id on failure."""
    ok = fail = 0
    mid = top_mid
    remaining = count
    while remaining > 0 and mid > 0:
        chunk_size = min(100, remaining)
        lo = max(1, mid - chunk_size + 1)
        ids = list(range(mid, lo - 1, -1))
        try:
            await bot.delete_messages(chat_id, ids)
            ok += len(ids)
        except Exception:
            for i in ids:
                try:
                    await bot.delete_message(chat_id, i)
                    ok += 1
                except Exception:
                    fail += 1
                await asyncio.sleep(0.03)
        mid = lo - 1
        remaining -= chunk_size
    return ok, fail


# ---------- /bind_gallery ----------
@router.message(Command("bind_gallery"))
async def bind_gallery(msg: Message, command: CommandObject, bot: Bot) -> None:
    """Bind the current group as gallery; optionally wipe last N messages."""
    if not await _is_admin(msg.from_user.id if msg.from_user else None):
        return
    try:
        wipe_n = int((command.args or "").strip() or "500")
    except Exception:
        wipe_n = 500
    wipe_n = max(0, min(wipe_n, 5000))

    await save_gallery_chat_id(msg.chat.id)
    async with session_scope() as s:
        await repo.set_setting(s, "widget_mids", {})
    await apply_gallery_commands(bot, msg.chat.id)

    status = await msg.reply(
        "✅ <b>Группа подключена как галерея</b>\n"
        + (f"⏳ Чищу последние {wipe_n} сообщений…" if wipe_n else "Готовлю виджеты…")
    )

    ok = fail = 0
    if wipe_n:
        ok, fail = await _bulk_delete(bot, msg.chat.id, status.message_id - 1, wipe_n)
        async with session_scope() as s:
            tracked = await repo.gallery_tracked_message_ids(s)
            if tracked:
                await repo.clear_gallery_tracking(s, tracked)

    try:
        await refresh_dashboard(bot)
    except Exception:
        log.exception("widget_refresh_failed_on_bind")

    try:
        tail = f"\n🧹 очищено сообщений: <b>{ok}</b>" if wipe_n else ""
        await status.edit_text(
            "✅ <b>Галерея готова</b>" + tail +
            "\n\nСверху закреплены три виджета — сводка, очередь и запросы. "
            "Они обновляются сами, жить будут только здесь."
        )
    except Exception:
        pass


# ---------- /unbind_gallery ----------
@router.message(Command("unbind_gallery"))
async def unbind_gallery(msg: Message, bot: Bot) -> None:
    if not await _is_admin(msg.from_user.id if msg.from_user else None):
        return
    if not await _is_in_gallery(msg.chat.id):
        await msg.reply("⛔ Эта группа не привязана как галерея.")
        return
    async with session_scope() as s:
        await repo.set_setting(s, "gallery_chat_id", {})
        await repo.set_setting(s, "widget_mids", {})
    await clear_gallery_commands(bot, msg.chat.id)
    await msg.reply("⛔ Галерея отключена. Виджеты больше не обновляются.")


# ---------- /rebuild ----------
@router.message(Command("rebuild"))
async def rebuild_widgets(msg: Message, bot: Bot) -> None:
    if not await _is_admin(msg.from_user.id if msg.from_user else None):
        return
    if not await _is_in_gallery(msg.chat.id):
        return
    async with session_scope() as s:
        await repo.set_setting(s, "widget_mids", {})
    await refresh_dashboard(bot)
    await msg.reply("🔄 Виджеты собраны заново и закреплены сверху.")


# ---------- /wipe ----------
@router.message(Command("wipe"))
async def wipe_messages(msg: Message, command: CommandObject, bot: Bot) -> None:
    if not await _is_admin(msg.from_user.id if msg.from_user else None):
        return
    if not await _is_in_gallery(msg.chat.id):
        return
    try:
        n = int((command.args or "").strip())
    except Exception:
        await msg.reply(
            "Подскажи, сколько удалить:\n"
            "<code>/wipe 500</code> — последние 500 сообщений (максимум 5000)."
        )
        return
    n = max(1, min(n, 5000))
    status = await msg.reply(f"🧹 Чищу последние {n} сообщений…")
    ok, fail = await _bulk_delete(bot, msg.chat.id, status.message_id - 1, n)
    async with session_scope() as s:
        tracked = await repo.gallery_tracked_message_ids(s)
        if tracked:
            await repo.clear_gallery_tracking(s, tracked)
    try:
        await refresh_dashboard(bot)
    except Exception:
        pass
    try:
        await status.edit_text(f"✅ Удалено сообщений: <b>{ok}</b>")
    except Exception:
        pass


# ---------- /gallery_info ----------
@router.message(Command("gallery_info"))
async def gallery_info(msg: Message) -> None:
    chat_id = await resolve_gallery_chat_id()
    async with session_scope() as s:
        widgets = await repo.get_setting(s, "widget_mids") or {}
    if not chat_id:
        await msg.reply(
            "ℹ️ Галерея пока не подключена.\n"
            "Админ может подключить эту группу командой <code>/bind_gallery</code>."
        )
        return
    here = chat_id == msg.chat.id
    if not here:
        await msg.reply("ℹ️ Эта группа не является галереей — активная подключена в другом чате.")
        return
    if widgets:
        state = f"📌 Виджетов активно: <b>{len(widgets)}</b> (сводка, очередь, запросы)"
    else:
        state = "<i>Виджеты ещё не собраны — обновятся в ближайшую минуту.</i>"
    await msg.reply("✅ <b>Галерея активна</b>\n" + state)


# ---------- /find ----------
@router.message(Command("find"))
async def find_order(msg: Message, command: CommandObject) -> None:
    """Return gallery links to photos of an order (public, no role check)."""
    if not await _is_in_gallery(msg.chat.id):
        return
    q = (command.args or "").strip().lstrip("#")
    if not q:
        await msg.reply(
            "🔎 Укажи номер заказа:\n"
            "<code>/find A1234</code>"
        )
        return
    async with session_scope() as s:
        o = await repo.get_order_by_number(s, q)
        if not o:
            await msg.reply(f"Не нашёл заказ <code>#{q}</code>.")
            return
        photos = await repo.photos_for_order(s, o.id)
    head = f"📦 <b>#{o.number}</b>"
    if o.device:
        head += f" — {o.device}"
    if not photos:
        await msg.reply(head + "\n\nФото ещё нет.")
        return
    links = [
        lk for p in photos
        if p.gallery_message_id
        and (lk := await gallery_photo_link(p.gallery_message_id))
    ]
    head += f"\nФото всего: <b>{len(photos)}</b>"
    if not links:
        await msg.reply(head + "\n<i>Старые фото — загружены до того, как группу подключили, ссылок на них нет.</i>")
        return
    body = "\n".join(f"• {lk}" for lk in links[:20])
    tail = f"\n<i>…и ещё {len(links) - 20}</i>" if len(links) > 20 else ""
    await msg.reply(head + "\n" + body + tail, disable_web_page_preview=True)


# ---------- /stats ----------
@router.message(Command("stats"))
async def group_stats(msg: Message) -> None:
    if not await _is_in_gallery(msg.chat.id):
        return
    text = await render_stats(None)
    await msg.reply(text)
