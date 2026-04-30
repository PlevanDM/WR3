from __future__ import annotations
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.logger import setup_logging, log
from app.bot.handlers import register_all
from app.bot.middlewares import UserMiddleware, ThrottleMiddleware
from app.bot.commands import apply_default_commands, apply_commands_for_all_known
from app.bot.services.feed import flush_events
from app.bot.services.sla import send_digest
from app.bot.services.widgets import refresh_dashboard
from app.db.session import engine
from app.remonline.poller import poll_once


async def _run_poll(bot: Bot) -> None:
    n = await poll_once()
    if n:
        await flush_events(bot)


async def _run_feed(bot: Bot) -> None:
    await flush_events(bot)


async def _run_dashboard(bot: Bot) -> None:
    """Keep group dashboards fresh even when no events fire."""
    await refresh_dashboard(bot)


async def main() -> None:
    setup_logging()
    log.info("starting", admins=settings.admin_ids)

    if settings.use_redis:
        from aiogram.fsm.storage.redis import RedisStorage
        from redis.asyncio import Redis
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        storage = RedisStorage(redis=redis)
    else:
        redis = None
        storage = MemoryStorage()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=storage)

    dp.message.middleware(ThrottleMiddleware(rate=0.3))
    dp.callback_query.middleware(ThrottleMiddleware(rate=0.3))
    dp.message.middleware(UserMiddleware())
    dp.callback_query.middleware(UserMiddleware())
    register_all(dp)

    await apply_default_commands(bot)
    await apply_commands_for_all_known(bot)

    sched = AsyncIOScheduler(timezone=settings.tz)
    sched.add_job(_run_poll, "interval", seconds=settings.poll_interval_sec, args=[bot], max_instances=1)
    sched.add_job(_run_feed, "interval", seconds=15, args=[bot], max_instances=1)
    sched.add_job(_run_dashboard, "interval", minutes=2, args=[bot], max_instances=1, id="dashboard")
    sched.add_job(send_digest, "cron", hour=9, minute=0, args=[bot], max_instances=1, id="sla_digest")
    sched.start()

    # Fire-and-forget initial dashboard refresh so widgets appear right after
    # startup without waiting for the 2-minute interval.
    asyncio.create_task(refresh_dashboard(bot))

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True,
        )
    finally:
        sched.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()
        if redis is not None:
            await redis.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
