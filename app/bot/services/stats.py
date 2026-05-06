from __future__ import annotations

from app.db.session import session_scope
from app.db import repo
from app.db.models import Role, User
from app.bot.texts import REQUEST_LABELS
from app.bot.services.settings_cache import finished_status_ids, sla_days, fresh_days


async def render_stats(user: User | None) -> str:
    fdays = await fresh_days()
    sdays = await sla_days()
    finished = await finished_status_ids()
    role = (user.role if user else None)

    async with session_scope() as s:
        so = await repo.stats_orders(
            s, fresh_days=fdays, exclude_status_ids=finished, stale_days=sdays,
        )
        qcount = await repo.count_queue(s, exclude_status_ids=finished, fresh_days=fdays)
        sreq = await repo.stats_requests(s, fresh_days=fdays)
        mine = None
        if role == Role.engineer and user:
            mine = await repo.stats_engineer(
                s, user.id, fresh_days=fdays, exclude_status_ids=finished,
            )

    head = "📊 <b>Сводка по сервису</b>"

    orders = (
        "\n\n<b>Заказы</b>"
        f"\n• всего в работе: <b>{so['total']}</b>"
        f"\n• без фото: <b>{so['no_photos']}</b>"
        f"\n• без движения &gt; {sdays} дн.: <b>{so['stale']}</b>"
        f"\n• без движения &gt; 30 дн.: <b>{so.get('stale_30', 0)}</b>"
        f"\n• без движения &gt; 6 мес.: <b>{so.get('stale_180', 0)}</b>"
        f"\n• принято сегодня: <b>{so['today']}</b>"
        f" · за неделю: <b>{so['week']}</b>"
        f"\n• движение сегодня: <b>{so.get('activity_today', 0)}</b>"
        f" · за неделю: <b>{so.get('activity_week', 0)}</b>"
        f"\n• в очереди на ремонт: <b>{qcount}</b>"
        f"\n• на инженерах: <b>{so.get('assigned', 0)}</b>"
        f" · не назначено: <b>{so.get('unassigned', 0)}</b>"
        f"\n• гарантийные: <b>{so.get('warranty', 0)}</b>"
        f" · платные: <b>{so.get('paid', 0)}</b>"
    )
    status_top = so.get("status_top") or []
    statuses_block = "\n\n<b>Статусы (топ)</b>"
    if status_top:
        for row in status_top:
            statuses_block += f"\n• {row['status']}: <b>{row['count']}</b>"
    else:
        statuses_block += "\n• —"

    req = (
        "\n\n<b>Запросы менеджеру</b>"
        f"\n• ждут ответа: <b>{sreq.get('open', 0)}</b>"
        f"\n• в работе: <b>{sreq.get('in_progress', 0)}</b>"
        f"\n• подтверждены: <b>{sreq.get('done', 0)}</b>"
        f"\n• отклонены: <b>{sreq.get('rejected', 0)}</b>"
    )
    by_type = sreq.get("by_type") or {}
    active = [(REQUEST_LABELS.get(k, k), v) for k, v in by_type.items() if v]
    if active:
        req += "\n• сейчас открыто: " + " · ".join(
            f"{label} <b>{v}</b>" for label, v in active
        )

    mine_block = ""
    if role == Role.engineer and mine is not None:
        mine_block = (
            "\n\n<b>Ты сейчас</b>"
            f"\n• заказов на тебе: <b>{mine['mine']}</b>"
            f"\n• твоих запросов открыто: <b>{mine['req_open']}</b>"
            f" · подтверждено: <b>{mine['req_done']}</b>"
        )

    footer = f"\n\n<i>окно: последние {fdays} дн.</i>"

    return head + orders + statuses_block + req + mine_block + footer
