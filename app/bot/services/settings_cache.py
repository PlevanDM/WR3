from __future__ import annotations
from app.config import settings as s_env
from app.db.session import session_scope
from app.db import repo


async def finished_status_ids() -> list[int]:
    async with session_scope() as s:
        v = await repo.get_setting(s, "finished_status_ids")
    ids = (v or {}).get("ids") or []
    return [int(x) for x in ids]


async def sla_days() -> int:
    async with session_scope() as s:
        v = await repo.get_setting(s, "sla_stale_days")
    return int((v or {}).get("value") or s_env.sla_stale_days)


async def fresh_days() -> int:
    async with session_scope() as s:
        v = await repo.get_setting(s, "fresh_window_days")
    return int((v or {}).get("value") or s_env.fresh_window_days)


async def set_fresh_days(n: int) -> None:
    async with session_scope() as s:
        await repo.set_setting(s, "fresh_window_days", {"value": int(n)})


async def set_finished_status_ids(ids: list[int]) -> None:
    async with session_scope() as s:
        await repo.set_setting(s, "finished_status_ids", {"ids": list(ids)})


# ---- auto status switching on manager reject (ASBIS/IT4) ----
async def auto_status_no_repair_id() -> int | None:
    """RO status id to set on order when ASBIS/IT4 request is rejected.

    Returns None when not configured — auto-switch is silently skipped.
    """
    async with session_scope() as s:
        v = await repo.get_setting(s, "auto_status_no_repair_id")
    val = (v or {}).get("value")
    return int(val) if val else None


async def set_auto_status_no_repair_id(status_id: int | None) -> None:
    async with session_scope() as s:
        await repo.set_setting(s, "auto_status_no_repair_id", {"value": int(status_id) if status_id else None})


# ---- paid-repair detection ----
# Stored as {"kind_ids": [int, ...], "name_substrings": ["платн", "out-of-warranty"]}
# Either kind_of_good_id match OR substring (case-insensitive) in status_name marks
# the order as paid. Empty config = no auto-detection.
async def paid_marker() -> dict:
    async with session_scope() as s:
        v = await repo.get_setting(s, "paid_marker")
    return v or {"kind_ids": [], "name_substrings": []}


async def set_paid_marker(*, kind_ids: list[int] | None = None,
                          name_substrings: list[str] | None = None) -> None:
    cur = await paid_marker()
    if kind_ids is not None:
        cur["kind_ids"] = list(kind_ids)
    if name_substrings is not None:
        cur["name_substrings"] = [str(x) for x in name_substrings]
    async with session_scope() as s:
        await repo.set_setting(s, "paid_marker", cur)


def is_paid_by_marker(raw: dict | None, status_name: str | None, marker: dict) -> bool:
    """Pure function: decide if RO order is paid based on marker config."""
    if not marker:
        return False
    if not raw and not status_name:
        return False
    raw = raw or {}
    kind_ids = set(int(x) for x in (marker.get("kind_ids") or []))
    if kind_ids:
        # RemOnline carries the kind in `kind_of_good_id` and/or nested objects.
        candidates = [
            raw.get("kind_of_good_id"),
            (raw.get("kind_of_good") or {}).get("id") if isinstance(raw.get("kind_of_good"), dict) else None,
            (raw.get("type") or {}).get("id") if isinstance(raw.get("type"), dict) else None,
        ]
        for c in candidates:
            try:
                if c is not None and int(c) in kind_ids:
                    return True
            except (TypeError, ValueError):
                continue
    subs = [s.lower().strip() for s in (marker.get("name_substrings") or []) if s and s.strip()]
    if subs and status_name:
        low = status_name.lower()
        if any(sub in low for sub in subs):
            return True
    return False
