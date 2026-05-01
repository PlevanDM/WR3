from __future__ import annotations
from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton,
)

from app.db.session import session_scope
from app.db import repo
from app.db.models import Role, User, PhotoKind
from app.bot.services.gallery import post_media, gallery_photo_link, resolve_gallery_chat_id
from app.bot.services.sync_ro import push_photos_comment
from app.bot import views

router = Router(name="reception")


class PhotoFSM(StatesGroup):
    uploading = State()


def _allowed(user: User | None) -> bool:
    return bool(user and user.is_active and user.role in (Role.reception, Role.admin, Role.owner))

def _allowed_to_write(user: User | None) -> bool:
    return bool(user and user.is_active and user.role in (Role.reception, Role.admin))


def _upload_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Готово", callback_data="rcp:done"),
        InlineKeyboardButton(text="❎ Отмена", callback_data="rcp:cancel"),
    ]])


@router.message(Command("photo"))
@router.message(StateFilter(None), F.text.in_({"📷 Без фото", "Без фото"}))
async def list_no_photos(msg: Message, user: User | None, bot: Bot) -> None:
    if not _allowed(user): return
    try: await bot.send_chat_action(msg.chat.id, "typing")
    except Exception: pass
    text, kb = await views.render_reception_nophoto_view(user, page=0)
    await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("dp:np:"))
async def cb_nophoto_page(cb: CallbackQuery, user: User | None) -> None:
    if not _allowed(user):
        await cb.answer("🔒", show_alert=True); return
    page = int(cb.data.split(":")[2])
    text, kb = await views.render_reception_nophoto_view(user, page=page)
    try: await cb.message.edit_text(text, reply_markup=kb)
    except Exception: pass
    await cb.answer()


@router.message(StateFilter(None), F.text.in_({"⏰ Застряли", "Застряли", "⏰ Старше 7 дней", "Старше 7 дней"}))
async def list_stale(msg: Message, user: User | None, bot: Bot) -> None:
    if not _allowed(user): return
    try: await bot.send_chat_action(msg.chat.id, "typing")
    except Exception: pass
    text, kb = await views.render_reception_stale_view(user, page=0)
    await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("dp:st:"))
async def cb_stale_page(cb: CallbackQuery, user: User | None) -> None:
    if not _allowed(user):
        await cb.answer("🔒", show_alert=True); return
    page = int(cb.data.split(":")[2])
    text, kb = await views.render_reception_stale_view(user, page=page)
    try: await cb.message.edit_text(text, reply_markup=kb)
    except Exception: pass
    await cb.answer()


@router.callback_query(F.data.startswith("rcp:photo:"))
async def start_photos(cb: CallbackQuery, user: User | None, state: FSMContext) -> None:
    if not _allowed_to_write(user):
        await cb.answer("🔒 У вас режим только для чтения", show_alert=True); return
    order_id = int(cb.data.split(":")[2])
    await state.set_state(PhotoFSM.uploading)
    await state.update_data(order_id=order_id, photos=[], documents=[])
    g = await resolve_gallery_chat_id()
    warn = ""
    if not g:
        warn = (
            "\n\n<i>Галерея ещё не подключена — фото сохранятся, но ссылок в "
            "общую группу не будет. Админу: /bind_gallery.</i>"
        )
    await cb.message.answer(
        "📤 <b>Загрузка фото</b>\n"
        "Отправляй снимки и файлы — можно альбомом, можно в несколько подходов.\n"
        "Когда закончишь — «✅ Готово»." + warn,
        reply_markup=_upload_kb(),
    )
    await cb.answer()


@router.message(PhotoFSM.uploading, F.photo)
async def on_photo(msg: Message, user: User | None, state: FSMContext) -> None:
    if not _allowed(user): return
    fid = msg.photo[-1].file_id
    data = await state.get_data()
    photos = list(data.get("photos") or [])
    photos.append(fid)
    await state.update_data(photos=photos)
    # Throttle confirmations on albums (media_group_id present -> one reply per group)
    mgid = msg.media_group_id
    last = data.get("last_mgid")
    if mgid and mgid == last:
        return
    await state.update_data(last_mgid=mgid)
    total = len(photos) + len(data.get("documents") or [])
    await msg.answer(f"📷 принял · всего в пачке: <b>{total}</b>", reply_markup=_upload_kb())


@router.message(PhotoFSM.uploading, F.document)
async def on_document(msg: Message, user: User | None, state: FSMContext) -> None:
    if not _allowed(user): return
    fid = msg.document.file_id
    data = await state.get_data()
    docs = list(data.get("documents") or [])
    docs.append(fid)
    await state.update_data(documents=docs)
    mgid = msg.media_group_id
    last = data.get("last_mgid")
    if mgid and mgid == last:
        return
    await state.update_data(last_mgid=mgid)
    total = len(data.get("photos") or []) + len(docs)
    await msg.answer(f"📎 принял · всего в пачке: <b>{total}</b>", reply_markup=_upload_kb())


@router.message(PhotoFSM.uploading)
async def on_other(msg: Message) -> None:
    await msg.answer(
        "Жду фото или файл. Когда закончишь — «✅ Готово». Передумал — /cancel.",
        reply_markup=_upload_kb(),
    )


@router.callback_query(PhotoFSM.uploading, F.data == "rcp:cancel")
async def on_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try: await cb.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    await cb.message.answer("❎ Отменил — ничего не сохранил.")
    await cb.answer()


@router.callback_query(PhotoFSM.uploading, F.data == "rcp:done")
async def on_done(cb: CallbackQuery, user: User | None, state: FSMContext, bot: Bot) -> None:
    if not _allowed_to_write(user):
        await cb.answer("🔒 У вас режим только для чтения", show_alert=True); return
    data = await state.get_data()
    await state.clear()
    order_id = int(data["order_id"])
    photos = list(data.get("photos") or [])
    docs = list(data.get("documents") or [])
    if not photos and not docs:
        await cb.message.answer("Ничего не пришло — пока нечего сохранять.")
        await cb.answer(); return

    try: await cb.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    try: await bot.send_chat_action(cb.message.chat.id, "upload_photo")
    except Exception: pass

    async with session_scope() as s:
        for fid in photos:
            await repo.add_photo(s, order_id, PhotoKind.other, fid,
                                 chat_id=None, message_id=None,
                                 uploaded_by=user.id, media_type="photo")
        for fid in docs:
            await repo.add_photo(s, order_id, PhotoKind.other, fid,
                                 chat_id=None, message_id=None,
                                 uploaded_by=user.id, media_type="document")
        order = await repo.get_order(s, order_id)
    number = order.number if order else order_id
    device = (order.device or "").strip() if order else ""
    serial = (order.serial or "").strip() if order else ""
    parts = [f"📷 #{number}"]
    if device: parts.append(device)
    if serial: parts.append(f"🔢 {serial}")
    cap = " · ".join(parts)
    mapping = await post_media(bot, order_id, photos=photos, documents=docs, caption=cap)

    links: list[str] = []
    if mapping:
        async with session_scope() as s:
            all_photos = await repo.photos_for_order(s, order_id)
            for p in all_photos:
                mid = mapping.get(p.tg_file_id)
                if mid:
                    p.gallery_message_id = mid
                    link = await gallery_photo_link(mid)
                    if link: links.append(link)

    if links:
        await push_photos_comment(order_id, links, author=user.full_name or str(user.tg_id))

    count = len(photos) + len(docs)
    async with session_scope() as s:
        await repo.add_event(s, "photos_added", order_id=order_id,
                             payload={"number": number, "count": count})
    summary = f"📷 {len(photos)}" + (f" · 📎 {len(docs)}" if docs else "")
    if mapping:
        tail = f"\nВ галерею улетело: <b>{len(mapping)}</b>"
    else:
        tail = "\n<i>Галерея не подключена — фото сохранил только здесь.</i>"
    await cb.message.answer(f"✅ Готово: {summary}{tail}")
    await cb.answer()
