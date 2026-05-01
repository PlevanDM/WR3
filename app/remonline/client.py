from __future__ import annotations
import asyncio
from typing import Any, Optional

import httpx

from app.config import settings


class RemOnlineError(Exception):
    pass


def _flatten_form(d: dict, prefix: str = "") -> dict:
    """Flatten a nested dict to a form-friendly flat dict using bracket notation,
    e.g. asset={"brand":"Apple"} → {"asset[brand]": "Apple"}.
    """
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, dict):
            out.update(_flatten_form(v, key))
        elif isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    out.update(_flatten_form(item, f"{key}[{i}]"))
                else:
                    out[f"{key}[{i}]"] = item
        elif v is None:
            continue
        else:
            out[key] = v
    return out


class RemOnlineClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
        self._api_key = api_key or settings.remonline_api_key
        self._base_url = (base_url or settings.remonline_base_url).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "RemOnlineClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}

    async def _request(self, method: str, path: str, *, params: dict | None = None,
                       json: Any = None, data: Any = None, attempts: int = 4) -> Any:
        last: Exception | None = None
        for i in range(attempts):
            try:
                r = await self._client.request(method, path, params=params, json=json,
                                               data=data, headers=self._headers())
                if r.status_code == 429:
                    wait = float(r.headers.get("Retry-After", "2"))
                    await asyncio.sleep(wait)
                    continue
                if 500 <= r.status_code < 600:
                    raise RemOnlineError(f"{r.status_code}: {r.text[:200]}")
                if r.status_code >= 400:
                    raise RemOnlineError(f"{r.status_code}: {r.text[:500]}")
                if not r.content:
                    return None
                return r.json()
            except (httpx.TransportError, RemOnlineError) as e:
                last = e
                await asyncio.sleep(min(2 ** i, 10))
        raise RemOnlineError(f"request failed: {last}")

    # ---- orders ----
    async def list_orders(self, *, page: int = 1, sort: str = "-modified_at", **filters: Any) -> dict:
        params: dict[str, Any] = {"page": page, "sort": sort}
        for k, v in filters.items():
            if v is None:
                continue
            params[k] = v
        return await self._request("GET", "/orders", params=params)

    async def iter_orders(self, *, sort: str = "-modified_at", max_pages: int = 20, **filters: Any):
        for p in range(1, max_pages + 1):
            data = await self.list_orders(page=p, sort=sort, **filters)
            items = (data or {}).get("data") or []
            if not items:
                break
            for it in items:
                yield it
            if len(items) < 50:
                break

    async def get_order(self, order_id: int) -> dict:
        return await self._request("GET", f"/orders/{order_id}")

    async def create_order_comment(self, order_id: int, message: str) -> dict:
        """Attach a plain-text comment to an order.

        RemOnline's endpoint expects the body keyed as `comment` (validation
        error "Це поле є обов'язковим" without it); it does NOT accept
        `text`/`message`/`body`.
        """
        return await self._request("POST", f"/orders/{order_id}/comments",
                                   json={"comment": message})

    async def create_order(self, payload: dict, *, as_form: bool = False) -> dict:
        """Create a new order in RemOnline."""
        if as_form:
            return await self._request("POST", "/orders", data=_flatten_form(payload))
        return await self._request("POST", "/orders", json=payload)

    async def update_order(self, order_id: int, payload: dict) -> dict:
        return await self._request("PATCH", f"/orders/{order_id}", json=payload)

    async def order_public_url(self, order_id: int) -> dict:
        return await self._request("GET", f"/orders/{order_id}/public-url")

    # ---- references ----
    async def list_order_statuses(self) -> list[dict]:
        r = await self._request("GET", "/statuses/orders")
        return (r or {}).get("data") or r or []

    async def list_employees(self) -> list[dict]:
        r = await self._request("GET", "/employees")
        return (r or {}).get("data") or r or []

    async def list_order_custom_fields(self) -> list[dict]:
        r = await self._request("GET", "/orders/custom-fields")
        return (r or {}).get("data") or r or []
