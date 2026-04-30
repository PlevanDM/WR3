from __future__ import annotations
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.db.session import session_scope
from app.db import repo
from app.db.models import Role


INVITE_TTL_HOURS = 72
_PREFIX = "invite:"


def _key(token: str) -> str:
    return f"{_PREFIX}{token}"


async def create_invite(role: Role, *, created_by: int) -> str:
    """Create a single-use invite token for the given role. Returns the token."""
    token = secrets.token_urlsafe(9)
    async with session_scope() as s:
        await repo.set_setting(s, _key(token), {
            "role": role.value,
            "used": False,
            "by": int(created_by),
            "at": datetime.now(timezone.utc).isoformat(),
        })
    return token


async def consume_invite(token: str) -> Optional[Role]:
    """Validate and mark invite as used. Returns the Role if valid, else None."""
    async with session_scope() as s:
        data = await repo.get_setting(s, _key(token))
        if not data or data.get("used"):
            return None
        try:
            created_at = datetime.fromisoformat(data.get("at"))
        except Exception:
            created_at = datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created_at > timedelta(hours=INVITE_TTL_HOURS):
            return None
        try:
            role = Role(data.get("role"))
        except Exception:
            return None
        data["used"] = True
        data["used_at"] = datetime.now(timezone.utc).isoformat()
        await repo.set_setting(s, _key(token), data)
    return role
