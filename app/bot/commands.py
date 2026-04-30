from __future__ import annotations
from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeChat,
    BotCommandScopeChatAdministrators,
    BotCommandScopeDefault,
)

from app.db.session import session_scope
from app.db.models import Role
from app.db import repo
from app.bot.services.gallery import resolve_gallery_chat_id


BASE = [
    BotCommand(command="start",  description="Открыть меню"),
    BotCommand(command="whoami", description="Кто я"),
    BotCommand(command="myid",   description="Мой Telegram ID"),
    BotCommand(command="cancel", description="Отменить текущее действие"),
]

ROLE_EXTRA: dict[Role, list[BotCommand]] = {
    Role.reception: [
        BotCommand(command="photo", description="📷 Заказы без фото"),
        BotCommand(command="order", description="🔎 Найти заказ"),
        BotCommand(command="stats", description="📊 Сводка"),
    ],
    Role.engineer: [
        BotCommand(command="queue", description="📋 Очередь"),
        BotCommand(command="mine",  description="🧰 Мои заказы"),
        BotCommand(command="order", description="🔎 Найти заказ"),
        BotCommand(command="myreq", description="🗂 Мои запросы"),
        BotCommand(command="stats", description="📊 Сводка"),
    ],
    Role.manager: [
        BotCommand(command="queue", description="📋 Очередь"),
        BotCommand(command="inbox", description="📨 Входящие"),
        BotCommand(command="order", description="🔎 Найти заказ"),
        BotCommand(command="stats", description="📊 Сводка"),
    ],
    Role.admin: [
        BotCommand(command="queue", description="📋 Очередь"),
        BotCommand(command="inbox", description="📨 Входящие"),
        BotCommand(command="order", description="🔎 Найти заказ"),
        BotCommand(command="stats", description="📊 Сводка"),
        BotCommand(command="admin", description="🛡 Админ-панель"),
    ],
}


GALLERY_PUBLIC_CMDS = [
    BotCommand(command="find",         description="🔎 Найти фото заказа"),
    BotCommand(command="stats",        description="📊 Сводка"),
    BotCommand(command="gallery_info", description="ℹ️ О галерее"),
]

GALLERY_ADMIN_CMDS = GALLERY_PUBLIC_CMDS + [
    BotCommand(command="bind_gallery",   description="📎 Подключить эту группу"),
    BotCommand(command="unbind_gallery", description="⛔ Отключить галерею"),
    BotCommand(command="rebuild",        description="🔄 Пересобрать виджеты"),
    BotCommand(command="wipe",           description="🧹 Очистить последние сообщения"),
]


async def apply_default_commands(bot: Bot) -> None:
    """Set scope-aware command lists.

    * Default (fallback)         — empty.
    * All private chats          — BASE (user menu in DM).
    * All group chats            — empty (other groups bot might be in stay silent).
    * All chat administrators    — empty.
    * The bound gallery chat     — GALLERY_PUBLIC_CMDS (anyone in the gallery).
    * Gallery chat admins        — GALLERY_ADMIN_CMDS (bind/unbind/rebuild/wipe).
    """
    for sc in (BotCommandScopeDefault(),
               BotCommandScopeAllGroupChats(),
               BotCommandScopeAllChatAdministrators()):
        try:
            await bot.delete_my_commands(scope=sc)
        except Exception:
            pass
    await bot.set_my_commands(BASE, scope=BotCommandScopeAllPrivateChats())

    # gallery-specific commands live only in the bound group
    gid = await resolve_gallery_chat_id()
    if gid:
        try:
            await bot.set_my_commands(
                GALLERY_PUBLIC_CMDS, scope=BotCommandScopeChat(chat_id=gid),
            )
        except Exception:
            pass
        try:
            await bot.set_my_commands(
                GALLERY_ADMIN_CMDS,
                scope=BotCommandScopeChatAdministrators(chat_id=gid),
            )
        except Exception:
            pass


async def apply_gallery_commands(bot: Bot, chat_id: int) -> None:
    """Called right after /bind_gallery to light up commands in the new group."""
    try:
        await bot.set_my_commands(
            GALLERY_PUBLIC_CMDS, scope=BotCommandScopeChat(chat_id=chat_id),
        )
    except Exception:
        pass
    try:
        await bot.set_my_commands(
            GALLERY_ADMIN_CMDS,
            scope=BotCommandScopeChatAdministrators(chat_id=chat_id),
        )
    except Exception:
        pass


async def clear_gallery_commands(bot: Bot, chat_id: int) -> None:
    """Called from /unbind_gallery to hide commands in the former group."""
    for sc in (BotCommandScopeChat(chat_id=chat_id),
               BotCommandScopeChatAdministrators(chat_id=chat_id)):
        try:
            await bot.delete_my_commands(scope=sc)
        except Exception:
            pass


async def apply_commands_for_user(bot: Bot, tg_id: int, role: Role) -> None:
    cmds = list(BASE) + ROLE_EXTRA.get(role, [])
    try:
        await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=tg_id))
    except Exception:
        pass


async def apply_commands_for_all_known(bot: Bot) -> None:
    async with session_scope() as s:
        users = await repo.list_users(s)
    for u in users:
        if u.is_active:
            await apply_commands_for_user(bot, u.tg_id, u.role)
