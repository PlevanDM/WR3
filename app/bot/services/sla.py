from __future__ import annotations
from aiogram import Bot

from app.db.session import session_scope
from app.db import repo
from app.db.models import Role
from app.bot.services.settings_cache import finished_status_ids, sla_days, fresh_days
from app.logger import log


async def send_digest(bot: Bot) -> int:
    finished = await finished_status_ids()
    days = await sla_days()
    fdays = await fresh_days()
    async with session_scope() as s:
        stale = await repo.orders_stale(s, days=days, limit=30, exclude_status_ids=finished, fresh_days=fdays)
        no_photo = await repo.orders_without_photos(s, limit=30, exclude_status_ids=finished, fresh_days=fdays)
        users = await repo.list_users(s)

    if not stale and not no_photo:
        return 0

    lines = [f"📊 <b>Утренняя сводка</b>  ·  порог {days} дн., окно {fdays} дн."]
    if stale:
        lines.append(f"\n⏰ <b>Застряли</b> — {len(stale)}:")
        for o in stale[:10]:
            age = ""
            if o.last_activity_at:
                from datetime import datetime, timezone
                la = o.last_activity_at
                if la.tzinfo is None:
                    la = la.replace(tzinfo=timezone.utc)
                age = f" · {(datetime.now(timezone.utc) - la).days} дн."
            lines.append(f"• #{o.number} — {o.device or '—'}{age}")
        if len(stale) > 10: lines.append(f"<i>…и ещё {len(stale)-10}</i>")
    if no_photo:
        lines.append(f"\n📷 <b>Без фото</b> — {len(no_photo)}:")
        for o in no_photo[:10]:
            lines.append(f"• #{o.number} — {o.device or '—'}")
        if len(no_photo) > 10: lines.append(f"<i>…и ещё {len(no_photo)-10}</i>")

    text = "\n".join(lines)
    sent = 0
    targets = [u.tg_id for u in users if u.is_active and u.role in (Role.manager, Role.reception, Role.admin)]
    for tg_id in targets:
        try:
            await bot.send_message(tg_id, text, disable_web_page_preview=True)
            sent += 1
        except Exception:
            log.warning("sla_digest_failed", tg_id=tg_id)
    return sent
