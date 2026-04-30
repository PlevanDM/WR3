"""RemOnline write-back: every meaningful action becomes a comment in RO.

Each comment is signed with the actor in `[Имя · роль]` brackets so that
auditing «кто передал заказ / кто изменил приоритет» is trivial.
"""
from __future__ import annotations

from app.remonline.client import RemOnlineClient, RemOnlineError
from app.logger import log


async def _post(order_id: int, body: str) -> bool:
    try:
        async with RemOnlineClient() as ro:
            await ro.create_order_comment(order_id, body)
        return True
    except RemOnlineError as e:
        log.warning("ro_comment_failed", order_id=order_id, error=str(e))
        return False


def _sign(actor: str | None, body: str) -> str:
    return f"[{actor}] {body}" if actor else body


async def push_photos_comment(order_id: int, links: list[str], author: str | None = None) -> bool:
    if not links:
        return False
    return await _post(order_id, _sign(author, "TG фото:\n" + "\n".join(links)))


async def push_request_comment(order_id: int, rtype: str, payload: str, author: str | None = None) -> bool:
    return await _post(order_id, _sign(author, f"Запрос [{rtype}]: {payload}"))


async def push_request_status_comment(order_id: int, rtype: str, status: str,
                                      *, note: str | None = None, actor: str | None = None) -> bool:
    body = f"Запрос [{rtype}] → {status}"
    if note:
        body += f" | причина: {note}"
    return await _post(order_id, _sign(actor, body))


async def push_action_comment(order_id: int, action: str, *, actor: str | None = None,
                              detail: str | None = None) -> bool:
    """Generic action log: take/untake/assign/transfer/priority/etc."""
    body = f"⚙️ {action}"
    if detail:
        body += f" — {detail}"
    return await _post(order_id, _sign(actor, body))
