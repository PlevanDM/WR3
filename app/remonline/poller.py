from __future__ import annotations

from app.db.session import session_scope
from app.db import repo
from app.logger import log
from app.remonline.client import RemOnlineClient, RemOnlineError
from app.remonline.mapper import ro_order_to_dict
from app.bot.services.settings_cache import (
    finished_status_ids, paid_marker, is_paid_by_marker,
)


async def poll_once(notify=None) -> int:
    count = 0
    try:
        finished = set(await finished_status_ids())
        marker = await paid_marker()
        async with session_scope() as s:
            bootstrap = await repo.get_setting(s, "bootstrap_done")
        first_run = not bool(bootstrap)

        async with RemOnlineClient() as ro:
            if not finished:
                try:
                    statuses = await ro.list_order_statuses()
                    auto_finished = [int(r["id"]) for r in statuses
                                     if r.get("group") in (6, 7)]
                    if auto_finished:
                        async with session_scope() as s:
                            await repo.set_setting(s, "finished_status_ids",
                                                   {"ids": auto_finished})
                        finished = set(auto_finished)
                        log.info("ro_finished_auto", ids=auto_finished)
                except Exception:
                    log.warning("ro_statuses_autoload_failed")

            async for raw in ro.iter_orders(sort="-modified_at", max_pages=5):
                data = ro_order_to_dict(raw)
                data["is_paid"] = is_paid_by_marker(raw, data.get("status_name"), marker)
                is_new = False
                needs_url = False
                async with session_scope() as s:
                    existed = await repo.get_order(s, data["id"])
                    old_status = existed.status_id if existed else None
                    needs_url = not (existed and existed.public_url)
                    order, created = await repo.upsert_order(s, data)
                    is_new = created
                    status_id = data.get("status_id")
                    if created:
                        if status_id not in finished:
                            await repo.ensure_queue_item(s, order.id)
                        if not first_run:
                            await repo.add_event(s, "order_created", order_id=order.id,
                                                 payload={"number": order.number})
                    elif old_status != status_id:
                        if not first_run:
                            await repo.add_event(
                                s, "order_status_changed", order_id=order.id,
                                payload={"number": order.number, "status": data["status_name"]},
                            )
                        if status_id in finished:
                            await repo.remove_queue_item(s, order.id)
                if needs_url:
                    try:
                        r = await ro.order_public_url(data["id"])
                        url = (r or {}).get("url") if isinstance(r, dict) else None
                        if url:
                            async with session_scope() as s:
                                o = await repo.get_order(s, data["id"])
                                if o and not o.public_url:
                                    o.public_url = url
                    except Exception:
                        pass
                count += 1
                if notify:
                    await notify(data, is_new)

        if first_run and count:
            async with session_scope() as s:
                await repo.set_setting(s, "bootstrap_done", {"at": True, "seeded": count})
            log.info("ro_bootstrap_done", seeded=count)
    except RemOnlineError as e:
        log.warning("ro_poll_error", error=str(e))
    except Exception:
        log.exception("ro_poll_unexpected")
    return count
