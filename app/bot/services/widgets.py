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
import unicodedata
from typing import Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.session import session_scope
from app.db import repo
from app.bot.services.gallery import resolve_gallery_chat_id
from app.bot.services.settings_cache import finished_status_ids, fresh_days, sla_days
from app.bot.services.stats import render_stats
from app.bot.texts import REQUEST_LABELS, human_request_status
from app.logger import log


WIDGETS_KEY = "widget_mids"
STATS_PAGE = 10
STATS_FREEZE_KEY = "widget_stats_freeze_until"


# ---------- setting helpers ----------
async def _get_map(key: str) -> dict:
    async with session_scope() as s:
        v = await repo.get_setting(s, key)
    return dict(v or {})


async def _set_map(key: str, m: dict) -> None:
    async with session_scope() as s:
        await repo.set_setting(s, key, m)


async def set_stats_freeze(seconds: int) -> None:
    until = datetime.now(timezone.utc).timestamp() + max(0, int(seconds))
    async with session_scope() as s:
        await repo.set_setting(s, STATS_FREEZE_KEY, {"ts": until})


async def clear_stats_freeze() -> None:
    async with session_scope() as s:
        await repo.set_setting(s, STATS_FREEZE_KEY, {"ts": 0})


async def _stats_frozen() -> bool:
    async with session_scope() as s:
        row = await repo.get_setting(s, STATS_FREEZE_KEY)
    ts = float((row or {}).get("ts") or 0)
    return ts > datetime.now(timezone.utc).timestamp()


# ---------- send/edit upsert primitive ----------
async def _upsert_widget_message(
    bot: Bot, chat_id: int, key: str, text: str,
    kb: Optional[InlineKeyboardMarkup] = None, *, pin: bool = True,
) -> Optional[int]:
    """Edit the existing widget message under `key`; create if missing/deleted."""
    widgets = await _get_map(WIDGETS_KEY)
    mid = widgets.get(key)
    recreate = False
    if mid:
        try:
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=mid,
                reply_markup=kb, disable_web_page_preview=True,
                parse_mode=ParseMode.HTML,
            )
            return mid
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                return mid
            # Recreate only when message really disappeared / became uneditable.
            recreate = any(x in err for x in (
                "message to edit not found",
                "message can't be edited",
                "message identifier is not specified",
            ))
            if not recreate:
                log.warning("widget_edit_rejected", key=key, error=str(e), mid=mid)
                return mid
        except Exception:
            log.exception("widget_edit_failed", key=key)
            return mid
    try:
        msg = await bot.send_message(
            chat_id, text, reply_markup=kb,
            disable_web_page_preview=True, disable_notification=True,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        log.exception("widget_send_failed", key=key)
        return None
    if pin:
        try:
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        except Exception:
            pass
    # If we had to recreate, try to remove the stale previous widget message.
    if mid and recreate:
        try:
            await bot.delete_message(chat_id, int(mid))
        except Exception:
            pass
    widgets[key] = msg.message_id
    await _set_map(WIDGETS_KEY, widgets)
    return msg.message_id


def _now_label() -> str:
    return datetime.now(timezone.utc).strftime("%d.%m %H:%M UTC")


def _clean_text(s: str | None, *, limit: int = 40) -> str:
    """Compact user-facing text for widgets (strip control chars, normalize spaces)."""
    if not s:
        return "—"
    # Remove invisible formatting chars (e.g. LRM/RLM) and control noise.
    cleaned = "".join(ch for ch in s if unicodedata.category(ch) not in {"Cf", "Cc"})
    cleaned = " ".join(cleaned.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _accepted_by_from_raw(raw: dict | None) -> str | None:
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("_accepted_by_name"), str) and raw.get("_accepted_by_name").strip():
        return raw.get("_accepted_by_name").strip()
    for key in ("accepted_by", "receiver", "created_by_employee", "employee", "manager"):
        v = raw.get(key)
        if isinstance(v, dict):
            nm = v.get("name") or v.get("full_name") or v.get("title")
            if nm:
                return str(nm).strip()
        elif isinstance(v, str) and v.strip():
            return v.strip()
    aid = raw.get("_accepted_by_id")
    return None


def _device_owner_from_raw(raw: dict | None, client_name: str | None) -> str | None:
    """Extract real device owner (for corp clients this may differ from payer/client)."""
    if not isinstance(raw, dict):
        return client_name.strip() if isinstance(client_name, str) and client_name.strip() else None
    asset = raw.get("asset")
    if isinstance(asset, dict):
        owner = asset.get("owner")
        if isinstance(owner, dict):
            nm = owner.get("name") or owner.get("full_name") or owner.get("title")
            if isinstance(nm, str) and nm.strip():
                return nm.strip()
        elif isinstance(owner, str) and owner.strip():
            return owner.strip()
    client = raw.get("client")
    if isinstance(client, dict):
        nm = client.get("name") or " ".join(
            x for x in [client.get("first_name"), client.get("last_name")] if x
        )
        if isinstance(nm, str) and nm.strip():
            return nm.strip()
    payer = raw.get("payer")
    if isinstance(payer, dict):
        nm = payer.get("name")
        if isinstance(nm, str) and nm.strip():
            return nm.strip()
    return client_name.strip() if isinstance(client_name, str) and client_name.strip() else None


# ---------- rendering: dashboards ----------
async def _render_stats_widget() -> str:
    body = await render_stats(None)
    return f"{body}\n\n<i>обновлено {_now_label()}</i>"


def _stats_actions_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⏰ Без движения >7д", callback_data="gw:stale7:0")
    b.button(text="📷 Без фото", callback_data="gw:nophoto:0")
    b.button(text="📋 Очередь", callback_data="gw:queue:0")
    b.button(text="💰 Платные", callback_data="gw:paid:0")
    b.button(text="🛡 Гарантийные", callback_data="gw:warranty:0")
    b.adjust(2, 2, 1)
    return b.as_markup()


async def render_stats_main() -> tuple[str, InlineKeyboardMarkup]:
    return await _render_stats_widget(), _stats_actions_kb()


def _owner_line(o) -> str:
    owner = _device_owner_from_raw(getattr(o, "raw", None), o.client_name)
    return _clean_text(owner, limit=22) if owner else "—"


async def render_stats_drilldown(kind: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    finished = await finished_status_ids()
    fdays = await fresh_days()
    page = max(0, int(page))
    title = {
        "stale7": "⏰ Без движения > 7 дн.",
        "nophoto": "📷 Без фото",
        "queue": "📋 Очередь",
        "paid": "💰 Платные",
        "warranty": "🛡 Гарантийные",
    }.get(kind, "📊 Список")
    lines: list[str] = []
    total = 0
    pages = 1
    if kind == "queue":
        async with session_scope() as s:
            total = await repo.count_queue(s, exclude_status_ids=finished, fresh_days=fdays)
            rows = await repo.queue_page(
                s, offset=page * STATS_PAGE, limit=STATS_PAGE,
                exclude_status_ids=finished, fresh_days=fdays,
            )
        pages = max(1, (total + STATS_PAGE - 1) // STATS_PAGE)
        page = min(page, pages - 1)
        for q, o in rows:
            lines.append(
                f"{q.position}. <code>#{o.number}</code> · {_clean_text(o.device, limit=24)} · "
                f"👤 <b>{_owner_line(o)}</b>"
            )
    else:
        async with session_scope() as s:
            if kind == "stale7":
                rows_all = list(await repo.orders_stale(
                    s, days=max(1, await sla_days()), limit=500,
                    exclude_status_ids=finished, fresh_days=fdays,
                ))
            elif kind == "nophoto":
                rows_all = list(await repo.orders_without_photos(
                    s, limit=500, exclude_status_ids=finished, fresh_days=fdays,
                ))
            else:
                rows_q = await repo.queue_page(
                    s, offset=0, limit=500, exclude_status_ids=finished, fresh_days=fdays,
                )
                if kind == "paid":
                    rows_all = [o for _, o in rows_q if bool(getattr(o, "is_paid", False))]
                elif kind == "warranty":
                    rows_all = [o for _, o in rows_q if not bool(getattr(o, "is_paid", False))]
                else:
                    rows_all = []
        total = len(rows_all)
        pages = max(1, (total + STATS_PAGE - 1) // STATS_PAGE)
        page = min(page, pages - 1)
        window = rows_all[page * STATS_PAGE:(page + 1) * STATS_PAGE]
        for i, o in enumerate(window, start=page * STATS_PAGE + 1):
            lines.append(
                f"{i}. <code>#{o.number}</code> · {_clean_text(o.device, limit=24)} · "
                f"👤 <b>{_owner_line(o)}</b>"
            )
    body = "\n".join(lines) if lines else "<i>Пусто.</i>"
    text = (
        f"{title} · <b>{total}</b>\n"
        f"<i>стр. {page + 1}/{pages}</i>\n\n{body}\n\n"
        f"<i>окно: последние {fdays} дн. · обновлено {_now_label()}</i>"
    )
    b = InlineKeyboardBuilder()
    if page > 0:
        b.button(text="◀️", callback_data=f"gw:{kind}:{page - 1}")
    b.button(text=f"{page + 1}/{pages}", callback_data="d:noop")
    if page < pages - 1:
        b.button(text="▶️", callback_data=f"gw:{kind}:{page + 1}")
    b.button(text="⬅️ К сводке", callback_data="gw:stats:back")
    b.adjust(3, 1)
    return text, b.as_markup()


async def _render_queue_widget() -> str:
    finished = await finished_status_ids()
    fdays = await fresh_days()
    async with session_scope() as s:
        rows = await repo.queue_page(
            s, offset=0, limit=20, exclude_status_ids=finished, fresh_days=fdays,
        )
        total = await repo.count_queue(s, exclude_status_ids=finished, fresh_days=fdays)

    head = f"📋 <b>Очередь</b> · заказов: <b>{total}</b>"
    if not rows:
        body = "<i>Очередь пуста — все заказы разобраны.</i>"
    else:
        lines = []
        for q, o in rows:
            num = _clean_text(o.number, limit=18)
            dev = _clean_text(o.device, limit=44)
            owner = _device_owner_from_raw(getattr(o, "raw", None), o.client_name)
            owner_txt = _clean_text(owner, limit=18) if owner else "—"
            repair_kind = "💰 платный" if bool(getattr(o, "is_paid", False)) else "🛡 гарантийный"
            acc = _accepted_by_from_raw(getattr(o, "raw", None))
            # Keep assignee/intake label readable: short limits make IDs look like
            # "27…" which users interpret as wrong employee number.
            acc_part = f" · принял: {_clean_text(acc, limit=32)}" if acc else ""
            lines.append(
                f"{q.position}. #{num} — {dev} · {repair_kind} · 👤 <b>ВЛАДЕЛЕЦ:</b> <b>{owner_txt}</b>{acc_part}"
            )
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
        if not await _stats_frozen():
            stats_text, stats_kb = await render_stats_main()
            await _upsert_widget_message(bot, chat_id, "stats", stats_text, stats_kb)
        await _upsert_widget_message(bot, chat_id, "queue",    await _render_queue_widget(),    None)
        await _upsert_widget_message(bot, chat_id, "requests", await _render_requests_widget(), None)
    except Exception:
        log.exception("dashboard_refresh_failed")
