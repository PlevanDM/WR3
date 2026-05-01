from __future__ import annotations
from typing import Optional

from app.config import settings
from app.db.session import session_scope
from app.db import repo
from app.db.models import Order
from app.remonline.client import RemOnlineClient, RemOnlineError
from app.logger import log


async def _fetch_public_url(order_id: int) -> Optional[str]:
    try:
        async with RemOnlineClient() as ro:
            r = await ro.order_public_url(order_id)
    except RemOnlineError as e:
        err_str = str(e)
        if "403:" in err_str or "404:" in err_str:
            log.debug("ro_public_url_failed", order_id=order_id, error=err_str)
        else:
            log.warning("ro_public_url_failed", order_id=order_id, error=err_str)
        return None
    except Exception:
        log.exception("ro_public_url_unexpected", order_id=order_id)
        return None
    if isinstance(r, dict):
        return r.get("url") or (r.get("data") or {}).get("url")
    return None


async def resolve_ro_url(order_or_id: Order | int) -> str:
    """Return best-known URL opening a specific RemOnline order.

    Priority: cached Order.public_url > live API fetch (cached) > template fallback.
    """
    oid: int
    cached: Optional[str] = None
    if isinstance(order_or_id, Order):
        oid = int(order_or_id.id)
        cached = order_or_id.public_url
    else:
        oid = int(order_or_id)
        async with session_scope() as s:
            o = await repo.get_order(s, oid)
            if o:
                cached = o.public_url

    if cached:
        return cached

    url = await _fetch_public_url(oid)
    if url:
        async with session_scope() as s:
            o = await repo.get_order(s, oid)
            if o and not o.public_url:
                o.public_url = url
        return url

    return settings.order_url(oid)
