"""Group widgets: live-updated dashboards over the reception photo gallery.

Design
------
The gallery chat is intentionally kept minimal:

* **reception photos** are posted directly (see `gallery.post_media`) and form
  the actual «галерея принятых»;
* **three pinned dashboards** — 📊 Summary / 📋 Top queue / 📨 Active requests —
  are edited in place via this module. Stored under
  `Setting["widget_mids"]` as `{"stats": mid, "queue": mid, "requests": mid}`.

Everything else (per-order cards, event-by-event posts) lives in DM widgets
instead, so the group stays a photo stream + at-a-glance state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from app.db.session import session_scope
from app.db import repo
from app.bot.services.gallery import resolve_gallery_chat_id
from app.bot.services.settings_cache import finished_status_ids, fresh_days
from app.bot.services.stats import render_stats
from app.bot.texts import REQUEST_LABELS, human_request_status
from app.logger import log


WIDGETS_KEY = "widget_mids"


# ---------- setting helpers ----------
async def _get_map(key: str) -> dict:
    async with session_scope() as s:
        v = await repo.get_setting(s, key)
    return dict(v or {})


async def _set_map(key: str, m: dict) -> None:
    async with session_scope() as s:
        await repo.set_setting(s, key, m)


# ---------- send/edit upsert primitive ----------
async def _upsert_widget_message(
    bot: Bot, chat_id: int, key: str, text: str,
    kb: Optional[InlineKeyboardMarkup] = None, *, pin: bool = True,
) -> Optional[int]:
    """Edit the existing widget message under `key`; create if missing/deleted."""
    widgets = await _get_map(WIDGETS_KEY)
    mid = widgets.get(key)
    if mid:
        try:
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=mid,
                reply_markup=kb, disable_web_page_preview=True,
            )
            return mid
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return mid
            # message was deleted or can't be edited — drop id and resend.
        except Exception:
            log.exception("widget_edit_failed", key=key)
    try:
        msg = await bot.send_message(
            chat_id, text, reply_markup=kb,
            disable_web_page_preview=True, disable_notification=True,
        )
    except Exception:
        log.exception("widget_send_failed", key=key)
        return None
    if pin:
        try:
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        except Exception:
            pass
    widgets[key] = msg.message_id
    await _set_map(WIDGETS_KEY, widgets)
    return msg.message_id


def _now_label() -> str:
    return datetime.now(timezone.utc).strftime("%d.%m %H:%M UTC")


# ---------- rendering: dashboards ----------
async def _render_stats_widget() -> str:
    body = await render_stats(None)
    return f"{body}\n\n<i>обновлено {_now_label()}</i>"


async def _render_queue_widget() -> str:
    finished = await finished_status_ids()
    fdays = await fresh_days()
    async with session_scope() as s:
        rows = await repo.queue_page(
            s, offset=0, limit=10, exclude_status_ids=finished, fresh_days=fdays,
        )
        total = await repo.count_queue(s, exclude_status_ids=finished, fresh_days=fdays)

    head = f"📋 <b>Очередь</b> · заказов: <b>{total}</b>"
    if not rows:
        body = "<i>Очередь пуста — все заказы разобраны.</i>"
    else:
        lines = []
        for q, o in rows:
            dev = (o.device or "—").strip()[:42]
            lines.append(f"<b>{q.position}.</b> <code>#{o.number}</code> · {dev}")
        body = "\n".join(lines)
        if total > len(rows):
            body += f"\n<i>…показано первых {len(rows)} из {total}</i>"
    return f"{head}\n\n{body}\n\n<i>обновлено {_now_label()}</i>"


async def _render_requests_widget() -> str:
    async with session_scope() as s:
        rows = await repo.open_requests(s, include_in_progress=True)
    head = "📨 <b>Запросы менеджеру</b>"
    if not rows:
        return (f"{head}\n\n<i>Всё разобрано — ни одного открытого запроса.</i>"
                f"\n\n<i>обновлено {_now_label()}</i>")

    by_type: dict[str, list] = {}
    for r, o in rows:
        by_type.setdefault(r.type.value, []).append((r, o))

    parts = [f"{head} · открыто: <b>{len(rows)}</b>"]
    for t in ("asbis", "it4", "custom"):
        bucket = by_type.get(t) or []
        if not bucket:
            continue
        label = REQUEST_LABELS.get(t, t)
        parts.append(f"\n<b>{label}</b> · {len(bucket)}")
        for r, o in bucket[:6]:
            human = human_request_status(t, r.status.value)
            parts.append(f"• <code>#{o.number}</code> — {human}")
        if len(bucket) > 6:
            parts.append(f"  <i>…и ещё {len(bucket) - 6}</i>")
    parts.append(f"\n<i>обновлено {_now_label()}</i>")
    return "\n".join(parts)


async def refresh_dashboard(bot: Bot) -> None:
    """Update all three pinned dashboards. Safe to call often."""
    chat_id = await resolve_gallery_chat_id()
    if not chat_id:
        return
    try:
        await _upsert_widget_message(bot, chat_id, "stats",    await _render_stats_widget(),    None)
        await _upsert_widget_message(bot, chat_id, "queue",    await _render_queue_widget(),    None)
        await _upsert_widget_message(bot, chat_id, "requests", await _render_requests_widget(), None)
    except Exception:
        log.exception("dashboard_refresh_failed")
