from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from dateutil import parser as dtp


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = dtp.parse(str(v))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _first(*vals):
    for v in vals:
        if v not in (None, ""):
            return v
    return None


def ro_order_to_dict(o: dict) -> dict:
    asset = (o.get("asset") or {}) if isinstance(o.get("asset"), dict) else {}
    client = (o.get("client") or {}) if isinstance(o.get("client"), dict) else {}
    status = (o.get("status") or {}) if isinstance(o.get("status"), dict) else {}

    order_id = int(_first(o.get("id"), o.get("order_id")))
    number = str(_first(o.get("number"), o.get("id_label"), order_id))
    status_id = o.get("status_id") if isinstance(o.get("status_id"), int) else status.get("id")
    status_name = _first(o.get("status_name"), status.get("name"))

    client_name = _first(
        o.get("client_name"),
        client.get("name"),
        " ".join(x for x in [client.get("first_name"), client.get("last_name")] if x) or None,
    )
    device = _first(asset.get("title"), asset.get("name"), o.get("device"))
    serial = _first(asset.get("uid"), asset.get("serial"), asset.get("imei"), o.get("serial"))
    defect = _first(o.get("malfunction"), o.get("problem"), o.get("defect"), o.get("description"))

    created = _parse_dt(_first(o.get("created_at"), o.get("created")))
    modified = _parse_dt(_first(o.get("modified_at"), o.get("updated_at"), o.get("modified"), created))

    return {
        "id": order_id,
        "number": number,
        "status_id": status_id,
        "status_name": status_name,
        "client_name": client_name,
        "device": device,
        "serial": serial,
        "defect": defect,
        "created_at_ro": created,
        "last_activity_at": modified,
        "raw": o,
    }
