from aiogram import Dispatcher, F

from app.bot.handlers import common, admin, reception, engineer, manager, group, errors


def register_all(dp: Dispatcher) -> None:
    # Private-chat routers: only accept messages/callbacks from DM, never groups.
    private_only = F.chat.type == "private"
    for r in (common.router, admin.router, reception.router,
              engineer.router, manager.router):
        r.message.filter(private_only)
    dp.include_router(common.router)
    dp.include_router(admin.router)
    dp.include_router(reception.router)
    dp.include_router(engineer.router)
    dp.include_router(manager.router)
    dp.include_router(group.router)   # gallery-only commands
    dp.include_router(errors.router)
