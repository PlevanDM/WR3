from __future__ import annotations

from app.db.session import session_scope
from app.db import repo
from app.logger import log
from app.remonline.client import RemOnlineClient, RemOnlineError
from app.remonline.mapper import ro_order_to_dict
from app.bot.services.settings_cache import (
    finished_status_ids, paid_marker, is_paid_by_marker, ro_employee_aliases,
)


async def poll_once(notify=None) -> int:
    count = 0
    try:
        finished = set(await finished_status_ids())
        marker = await paid_marker()
        employees_by_id: dict[int, str] = {}
        local_employees_by_id: dict[int, str] = {}
        aliases_by_id: dict[int, str] = {}
        async with session_scope() as s:
            bootstrap = await repo.get_setting(s, "bootstrap_done")
            # Local fallback: users linked to RemOnline employee IDs.
            try:
                users = await repo.list_users(s)
                for u in users:
                    if u.ro_employee_id:
                        nm = u.full_name or (u.username and f"@{u.username}") or str(u.tg_id)
                        local_employees_by_id[int(u.ro_employee_id)] = nm
            except Exception:
                local_employees_by_id = {}
        try:
            aliases_by_id = await ro_employee_aliases()
        except Exception:
            aliases_by_id = {}
        first_run = not bool(bootstrap)

        async with RemOnlineClient() as ro:
            # Resolve employee IDs to human names so UI can show "кто принял".
            try:
                emps = await ro.list_employees()
                for e in emps or []:
                    try:
                        eid = int(e.get("id"))
                    except Exception:
                        continue
                    nm = (
                        e.get("name")
                        or e.get("full_name")
                        or e.get("title")
                        or e.get("display_name")
                    )
                    if nm:
                        employees_by_id[eid] = str(nm).strip()
            except Exception:
                employees_by_id = {}

            try:
                statuses = await ro.list_order_statuses()
                status_ids_now = {int(r["id"]) for r in statuses if r.get("id") is not None}
                # "Not in work" buckets for this project:
                # 4=done/ready, 6=issued/closed, 7=cancelled/disposed.
                auto_finished = [int(r["id"]) for r in statuses
                                 if r.get("group") in (4, 6, 7)]
                stale_config = bool(finished) and not (set(finished) & status_ids_now)
                if auto_finished and (not finished or stale_config):
                    async with session_scope() as s:
                        await repo.set_setting(s, "finished_status_ids",
                                               {"ids": auto_finished})
                    finished = set(auto_finished)
                    log.info("ro_finished_auto", ids=auto_finished, stale_config=stale_config)
            except Exception:
                log.warning("ro_statuses_autoload_failed")

            async for raw in ro.iter_orders(sort="-modified_at", max_pages=5):
                # Inject accepted-by hint into raw so views/widgets can show it.
                # Fallback chain: creator -> manager -> assignee.
                try:
                    # Some RO payloads may include expanded person objects.
                    for obj_key in ("created_by", "manager", "assignee"):
                        obj = raw.get(obj_key)
                        if not isinstance(obj, dict):
                            continue
                        obj_id = obj.get("id")
                        obj_name = (
                            obj.get("name")
                            or obj.get("full_name")
                            or obj.get("title")
                            or obj.get("display_name")
                        )
                        try:
                            if obj_id is not None and obj_name:
                                employees_by_id[int(obj_id)] = str(obj_name).strip()
                        except Exception:
                            pass
                    created_by = raw.get("created_by_id")
                    manager_id = raw.get("manager_id")
                    assignee_id = raw.get("assignee_id")
                    picked_id = None
                    for cand in (created_by, manager_id, assignee_id):
                        if cand is not None:
                            try:
                                picked_id = int(cand)
                                break
                            except Exception:
                                continue
                    if picked_id is not None:
                        raw["_accepted_by_id"] = picked_id
                        if picked_id in employees_by_id:
                            raw["_accepted_by_name"] = employees_by_id[picked_id]
                        elif picked_id in local_employees_by_id:
                            raw["_accepted_by_name"] = local_employees_by_id[picked_id]
                        elif picked_id in aliases_by_id:
                            raw["_accepted_by_name"] = aliases_by_id[picked_id]
                except Exception:
                    pass
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
