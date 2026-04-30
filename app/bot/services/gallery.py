from __future__ import annotations
from typing import Optional

from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputMediaDocument, Message

from app.config import settings
from app.db.session import session_scope
from app.db import repo


def _link_message(chat_id: int, message_id: int) -> str:
    internal = str(chat_id)
    if internal.startswith("-100"):
        internal = internal[4:]
    return f"https://t.me/c/{internal}/{message_id}"


async def resolve_gallery_chat_id() -> Optional[int]:
    if settings.gallery_chat_id:
        return settings.gallery_chat_id
    async with session_scope() as s:
        v = await repo.get_setting(s, "gallery_chat_id")
    return int(v["id"]) if v and "id" in v else None


async def save_gallery_chat_id(chat_id: int) -> None:
    async with session_scope() as s:
        await repo.set_setting(s, "gallery_chat_id", {"id": chat_id})


async def _send_group(bot: Bot, chat_id: int, items: list[tuple[str, str]],
                       caption: str, media_cls) -> list[tuple[str, int]]:
    """Send items chunked by 10. Return list of (file_id, message_id)."""
    out: list[tuple[str, int]] = []
    for chunk_start in range(0, len(items), 10):
        chunk = items[chunk_start:chunk_start + 10]
        if len(chunk) == 1:
            fid, _ = chunk[0]
            cap = caption if chunk_start == 0 else None
            if media_cls is InputMediaPhoto:
                m: Message = await bot.send_photo(chat_id, fid, caption=cap)
            else:
                m = await bot.send_document(chat_id, fid, caption=cap)
            out.append((fid, m.message_id))
            continue
        media = [
            media_cls(media=fid, caption=caption if (chunk_start == 0 and i == 0) else None)
            for i, (fid, _) in enumerate(chunk)
        ]
        msgs = await bot.send_media_group(chat_id, media)
        for (fid, _), m in zip(chunk, msgs):
            out.append((fid, m.message_id))
    return out


async def post_media(bot: Bot, order_id: int,
                     photos: list[str], documents: list[str],
                     caption: str) -> dict[str, int]:
    """Post photos and documents to gallery. Returns {file_id: message_id}."""
    chat_id = await resolve_gallery_chat_id()
    result: dict[str, int] = {}
    if not chat_id:
        return result
    if photos:
        pairs = [(fid, "photo") for fid in photos]
        sent = await _send_group(bot, chat_id, pairs, caption, InputMediaPhoto)
        for fid, mid in sent:
            result[fid] = mid
    if documents:
        doc_caption = caption if not photos else None
        pairs = [(fid, "document") for fid in documents]
        sent = await _send_group(bot, chat_id, pairs, doc_caption or "", InputMediaDocument)
        for fid, mid in sent:
            result[fid] = mid
    return result


async def post_album(bot: Bot, order_id: int, file_ids: list[str], caption: str) -> list[int]:
    """Legacy wrapper — treats all as photos."""
    r = await post_media(bot, order_id, photos=file_ids, documents=[], caption=caption)
    return [mid for _, mid in r.items()]


async def gallery_photo_link(message_id: int) -> Optional[str]:
    chat_id = await resolve_gallery_chat_id()
    if not chat_id:
        return None
    return _link_message(chat_id, message_id)
