from __future__ import annotations
from aiogram import Bot

_username: str | None = None


async def bot_username(bot: Bot) -> str:
    global _username
    if _username is None:
        me = await bot.get_me()
        _username = me.username or ""
    return _username


async def order_deep_link(bot: Bot, order_id: int) -> str | None:
    u = await bot_username(bot)
    if not u:
        return None
    return f"https://t.me/{u}?start=order_{order_id}"
