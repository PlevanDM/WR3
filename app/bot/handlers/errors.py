from __future__ import annotations
import html
import traceback

from aiogram import Router, Bot
from aiogram.types import ErrorEvent, Message, CallbackQuery

from app.config import settings
from app.logger import log

router = Router(name="errors")


@router.errors()
async def on_error(event: ErrorEvent, bot: Bot) -> bool:
    exc = event.exception
    log.exception("unhandled_error", error=str(exc))

    upd = event.update
    src_message: Message | None = None
    src_cb: CallbackQuery | None = None
    if getattr(upd, "message", None):
        src_message = upd.message
    elif getattr(upd, "callback_query", None):
        src_cb = upd.callback_query
        src_message = src_cb.message if src_cb else None

    try:
        if src_cb is not None:
            await src_cb.answer("⚠️ Что-то пошло не так. Уже смотрим.", show_alert=False)
        elif src_message is not None:
            await src_message.answer(
                "⚠️ Что-то пошло не так. Уже смотрим — попробуй ещё раз через минуту."
            )
    except Exception:
        pass

    tb = "".join(traceback.format_exception_only(type(exc), exc))[-1500:]
    text = f"💥 <b>Ошибка</b>\n<pre>{html.escape(tb)}</pre>"
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass
    return True
