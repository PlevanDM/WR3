from __future__ import annotations
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from app.db.session import session_scope
from app.db import repo
from app.logger import log


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user = None
        if isinstance(event, (Message, CallbackQuery)):
            tg_user = event.from_user

        user = None
        if tg_user is not None:
            try:
                async with session_scope() as s:
                    user = await repo.get_user_by_tg(s, tg_user.id)
                    if user:
                        override = await repo.get_role_override(s, tg_user.id)
                        user.real_role = user.role
                        if override is not None and override != user.role:
                            user.role = override
                        s.expunge(user)
            except Exception:
                log.exception("user_lookup_failed", tg_id=tg_user.id)
        data["user"] = user
        data["tg_user"] = tg_user
        return await handler(event, data)


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, rate: float = 0.4) -> None:
        self._rate = rate
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        uid = None
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            uid = event.from_user.id
        skip = False
        if isinstance(event, Message) and event.text:
            t = event.text.strip()
            if t in ("/start", "/cancel") or t.startswith("/start "):
                skip = True
        if uid is not None and not skip:
            now = time.monotonic()
            prev = self._last.get(uid, 0.0)
            if now - prev < self._rate:
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer("⏳ Помедленнее…", show_alert=False)
                    except Exception:
                        pass
                return None
            self._last[uid] = now
            if len(self._last) > 2048:
                cutoff = now - 60
                self._last = {k: v for k, v in self._last.items() if v > cutoff}
        return await handler(event, data)
