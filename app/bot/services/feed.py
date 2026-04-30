"""Event flush → group dashboards refresh.

The gallery chat is a photo stream plus three pinned dashboards. Events no
longer produce per-order group cards — DM widgets carry the interactive state
for each role. Here we just mark events as consumed and keep the dashboards
in sync whenever something changes.
"""
from __future__ import annotations

from aiogram import Bot

from app.db.session import session_scope
from app.db import repo
from app.bot.services.gallery import resolve_gallery_chat_id
from app.bot.services.widgets import refresh_dashboard
from app.logger import log


async def flush_events(bot: Bot, limit: int = 100) -> int:
    """Consume pending events and refresh dashboards.

    Returns the number of events marked as posted.
    """
    chat_id = await resolve_gallery_chat_id()
    if not chat_id:
        return 0

    async with session_scope() as s:
        events = await repo.pending_events(s, limit=limit)
    if not events:
        return 0

    for e in events:
        async with session_scope() as s:
            await repo.mark_event_posted(s, e.id, 0)

    try:
        await refresh_dashboard(bot)
    except Exception:
        log.exception("refresh_dashboard_failed")

    return len(events)
