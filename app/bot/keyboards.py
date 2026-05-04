from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from app.config import settings
from app.db.models import Role


# ===== reply main menu (per role) =====
BTN_QUEUE        = "📋 Очередь"
BTN_MINE         = "🧰 Мои заказы"
BTN_NEW_REQ      = "📨 Новый запрос"
BTN_MY_REQ       = "🗂 Мои запросы"
BTN_INBOX        = "📨 Входящие"
BTN_ASBIS        = "📋 Асбис"
BTN_IT4          = "📋 ИТ4"
BTN_NO_PHOTO     = "📷 Без фото"
BTN_STALE        = "⏰ Застряли"
BTN_STATS        = "📊 Статистика"
BTN_ADMIN        = "🛡 Админ"
BTN_FIND_ORDER   = "🔎 Заказ"


def main_menu(role: Role) -> ReplyKeyboardMarkup:
    """Per-role reply keyboard. Rows are fixed for predictability."""
    b = ReplyKeyboardBuilder()
    rows: list[int] = []

    if role == Role.reception:
        b.add(KeyboardButton(text=BTN_NO_PHOTO), KeyboardButton(text=BTN_STALE))
        b.add(KeyboardButton(text=BTN_FIND_ORDER), KeyboardButton(text=BTN_STATS))
        rows = [2, 2]

    elif role == Role.engineer:
        b.add(KeyboardButton(text=BTN_QUEUE), KeyboardButton(text=BTN_MINE))
        b.add(KeyboardButton(text=BTN_NEW_REQ), KeyboardButton(text=BTN_MY_REQ))
        b.add(KeyboardButton(text=BTN_FIND_ORDER), KeyboardButton(text=BTN_STATS))
        rows = [2, 2, 2]

    elif role == Role.manager:
        b.add(KeyboardButton(text=BTN_INBOX), KeyboardButton(text=BTN_QUEUE))
        b.add(KeyboardButton(text=BTN_ASBIS), KeyboardButton(text=BTN_IT4))
        b.add(KeyboardButton(text=BTN_NEW_REQ), KeyboardButton(text=BTN_FIND_ORDER))
        b.add(KeyboardButton(text=BTN_STATS))
        rows = [2, 2, 2, 1]

    elif role == Role.admin:
        b.add(KeyboardButton(text=BTN_ADMIN), KeyboardButton(text=BTN_QUEUE))
        b.add(KeyboardButton(text=BTN_INBOX), KeyboardButton(text=BTN_NEW_REQ))
        b.add(KeyboardButton(text=BTN_FIND_ORDER), KeyboardButton(text=BTN_STATS))
        rows = [2, 2, 2]

    elif role == Role.owner:
        b.add(KeyboardButton(text=BTN_INBOX), KeyboardButton(text=BTN_QUEUE))
        b.add(KeyboardButton(text=BTN_ASBIS), KeyboardButton(text=BTN_IT4))
        b.add(KeyboardButton(text=BTN_FIND_ORDER), KeyboardButton(text=BTN_STATS))
        rows = [2, 2, 2]

    b.adjust(*rows)
    return b.as_markup(resize_keyboard=True, is_persistent=True)


# ===== order card =====
def order_card_kb(order_id: int, *,
                  is_admin: bool = False,
                  can_take: bool = False,
                  can_request: bool = False,
                  for_reception: bool = False,
                  taken: bool = False,
                  bot_deep_link: str | None = None,
                  ro_url: str | None = None,
                  # legacy alias – keep old callers working
                  for_engineer: bool | None = None,
                  ) -> InlineKeyboardMarkup:
    """Inline card under an order message.

    `can_take`     — show 👤 Take / ↩️ Untake (engineers / admin).
    `can_request`  — show 📨 «Запрос по заказу» (engineer / manager / admin).
    `for_reception`— show 📷 Добавить фото.
    `is_admin`     — show queue re-ordering buttons.
    `for_engineer` (legacy) — sets both can_take and can_request.
    """
    if for_engineer is not None:
        can_take = can_take or for_engineer
        can_request = can_request or for_engineer

    b = InlineKeyboardBuilder()
    b.button(text="🔗 Открыть в RemOnline", url=ro_url or settings.order_url(order_id))
    if bot_deep_link:
        b.button(text="💬 В боте", url=bot_deep_link)
    sizes = [2 if bot_deep_link else 1]

    if for_reception:
        b.button(text="📷 Добавить фото", callback_data=f"rcp:photo:{order_id}")
        sizes.append(1)

    if can_take:
        if taken:
            b.button(text="↩️ Вернуть в очередь", callback_data=f"eng:untake:{order_id}")
        else:
            b.button(text="👤 Взять", callback_data=f"eng:take:{order_id}")
        sizes.append(1)

    if can_request:
        b.button(text="📨 Запрос по заказу", callback_data=f"eng:reqmenu:{order_id}")
        sizes.append(1)

    if is_admin:
        b.button(text="⬆️ Выше",  callback_data=f"adm:q:up:{order_id}")
        b.button(text="⬇️ Ниже",  callback_data=f"adm:q:dn:{order_id}")
        b.button(text="⏫ В начало", callback_data=f"adm:q:top:{order_id}")
        sizes.append(3)

    b.adjust(*sizes)
    return b.as_markup()


# ===== engineer request picker =====
def order_pick_kb(orders: list, *, section_mine: int = 0) -> InlineKeyboardMarkup:
    """List of orders to start a request from. First `section_mine` items are own."""
    b = InlineKeyboardBuilder()
    sizes: list[int] = []
    if section_mine:
        for o in orders[:section_mine]:
            label = f"🧰 #{o.number}" + (f" · {o.device[:22]}" if o.device else "")
            b.button(text=label, callback_data=f"eng:req:pick:{o.id}")
            sizes.append(1)
    for o in orders[section_mine:]:
        label = f"📋 #{o.number}" + (f" · {o.device[:22]}" if o.device else "")
        b.button(text=label, callback_data=f"eng:req:pick:{o.id}")
        sizes.append(1)
    b.button(text="🔎 По номеру…", callback_data="eng:req:bynum")
    b.button(text="❎ Отмена",      callback_data="eng:reqcancel")
    sizes += [1, 1]
    b.adjust(*sizes)
    return b.as_markup()


# ===== manager card =====
def request_card_kb(request_id: int, *, rtype: str | None = None) -> InlineKeyboardMarkup:
    """Default manager actions on a request card."""
    b = InlineKeyboardBuilder()
    if rtype in ("asbis", "it4", "outsource"):
        b.button(text="✅ Подтвердить",       callback_data=f"mgr:req:done:{request_id}")
        b.button(text="🚫 Отклонить с комм.", callback_data=f"mgr:req:rejc:{request_id}")
        b.adjust(2)
    else:
        b.button(text="⚙️ В работу", callback_data=f"mgr:req:ip:{request_id}")
        b.button(text="✅ Готово",    callback_data=f"mgr:req:done:{request_id}")
        b.button(text="🚫 Отклонить с комм.", callback_data=f"mgr:req:rejc:{request_id}")
        b.adjust(2, 1)
    return b.as_markup()


def request_type_kb(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🏷 Асбис",  callback_data=f"eng:req:asbis:{order_id}")
    b.button(text="📝 ИТ4",    callback_data=f"eng:req:it4:{order_id}")
    b.button(text="✏️ Другое", callback_data=f"eng:req:custom:{order_id}")
    b.button(text="⬅️ К выбору заказа", callback_data="eng:req:pickany")
    b.button(text="❎ Отмена",           callback_data="eng:reqcancel")
    b.adjust(3, 1, 1)
    return b.as_markup()


def request_type_paid_kb(order_id: int) -> InlineKeyboardMarkup:
    """Request menu for paid repairs: no ASBIS/IT4 — only outsource / paid engineer."""
    b = InlineKeyboardBuilder()
    b.button(text="💰 Платному инженеру", callback_data=f"eng:paid:assign:{order_id}")
    b.button(text="🌐 На аутсорс",         callback_data=f"eng:req:outsource:{order_id}")
    b.button(text="✏️ Другое",             callback_data=f"eng:req:custom:{order_id}")
    b.button(text="⬅️ К выбору заказа",    callback_data="eng:req:pickany")
    b.button(text="❎ Отмена",              callback_data="eng:reqcancel")
    b.adjust(1, 1, 1, 1, 1)
    return b.as_markup()


def mgr_reject_cancel_kb(rid: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Отмена", callback_data=f"mgr:req:rejc:cancel:{rid}")
    return b.as_markup()


# Pre-defined reject reasons: (code, short_label, full_comment)
REJECT_REASONS: list[tuple[str, str, str]] = [
    ("nodata", "Нет данных",       "Недостаточно данных — уточни и пришли запрос заново."),
    ("nowar",  "Вне гарантии",     "Вне гарантии — не проходит по условиям обслуживания."),
    ("dup",    "Дубликат",         "Дубликат — запрос по этому заказу уже есть."),
    ("wrong",  "Не тот тип",       "Неверный тип запроса для этого заказа."),
    ("imei",   "IMEI/SN не читается", "IMEI / серийник не читается — пришли корректный."),
    ("later",  "Позже",            "Сейчас не оформляем — отложим, вернёмся позже."),
]


def reject_reasons_kb(rid: int) -> InlineKeyboardMarkup:
    """Preset reject reasons + free-text fallback."""
    b = InlineKeyboardBuilder()
    for code, label, _ in REJECT_REASONS:
        b.button(text=f"🚫 {label}", callback_data=f"mgr:req:rejr:{rid}:{code}")
    b.button(text="✏️ Свой текст", callback_data=f"mgr:req:rejtxt:{rid}")
    b.button(text="⬅️ Отмена",     callback_data=f"mgr:req:rejc:cancel:{rid}")
    b.adjust(2, 2, 2, 1, 1)
    return b.as_markup()


def eng_custom_text_kb(order_id: int, *, allow_empty: bool = True) -> InlineKeyboardMarkup:
    """Keyboard for free-text request prompts.

    When allow_empty=False (e.g. outsource), the «Без текста» shortcut is hidden —
    a comment is required by business rules.
    """
    b = InlineKeyboardBuilder()
    if allow_empty:
        b.button(text="➡️ Отправить без текста",
                 callback_data=f"eng:req:customok:{order_id}")
    b.button(text="❎ Отмена", callback_data="eng:reqcancel")
    b.adjust(1)
    return b.as_markup()


def sla_days_kb(current: int | None = None) -> InlineKeyboardMarkup:
    """Preset buttons for SLA (stale) days."""
    b = InlineKeyboardBuilder()
    for n in (3, 5, 7, 10, 14, 21, 30, 45):
        mark = "✅ " if current == n else ""
        b.button(text=f"{mark}{n} дн.", callback_data=f"adm:sla:set:{n}")
    b.button(text="⬅️ Назад", callback_data="adm:settings")
    b.adjust(4, 4, 1)
    return b.as_markup()


def fresh_days_kb(current: int | None = None) -> InlineKeyboardMarkup:
    """Preset buttons for «fresh orders window» (days)."""
    b = InlineKeyboardBuilder()
    for n in (7, 14, 21, 30, 45, 60, 90, 180):
        mark = "✅ " if current == n else ""
        b.button(text=f"{mark}{n} дн.", callback_data=f"adm:fresh:set:{n}")
    b.button(text="⬅️ Назад", callback_data="adm:settings")
    b.adjust(4, 4, 1)
    return b.as_markup()


def my_request_kb(request_id: int, *, open_state: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if open_state:
        b.button(text="❎ Отменить запрос", callback_data=f"eng:reqown:cancel:{request_id}")
    b.adjust(1)
    return b.as_markup()


# ===== admin =====
def admin_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="👥 Пользователи", callback_data="adm:users")
    b.button(text="➕ Пригласить",   callback_data="adm:invite")
    b.button(text="🧪 Тест роли",    callback_data="adm:asrole")
    b.button(text="⚙️ Настройки",    callback_data="adm:settings")
    b.button(text="🩺 Диагностика",  callback_data="adm:diag")
    b.button(text="📢 Рассылка",     callback_data="adm:act:broadcast")
    b.button(text="🔄 Пересобрать виджеты", callback_data="adm:act:widgets")
    b.button(text="🧹 Очистить галерею",    callback_data="adm:act:galclear")
    b.button(text="🧹 Чистка N последних",  callback_data="adm:act:wipeN")
    b.button(text="🔄 Обновить виджеты сейчас", callback_data="adm:act:flush")
    b.button(text="🌱 Собрать очередь",     callback_data="adm:act:seed")
    b.button(text="🔁 Перенумеровать",      callback_data="adm:act:reindex")
    b.button(text="🏷 Статусы RemOnline",   callback_data="adm:act:rostatuses")
    sizes = [2, 2, 2, 2, 2, 2, 2]
    b.adjust(*sizes)
    return b.as_markup()


def invite_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔗 Ссылка-приглашение", callback_data="adm:inv:link")
    b.button(text="🆔 По Telegram ID",     callback_data="adm:inv:byid")
    b.button(text="⬅️ Назад", callback_data="adm:back")
    b.adjust(1)
    return b.as_markup()


def invite_role_pick_kb(mode: str) -> InlineKeyboardMarkup:
    """mode: 'link' or 'byid'."""
    b = InlineKeyboardBuilder()
    for role, label in [
        ("reception", "🛎 Приёмка"),
        ("engineer", "🛠 Инженер"),
        ("manager", "📋 Менеджер"),
        ("admin", "🛡 Админ"),
        ("owner", "👑 Владелец"),
    ]:
        b.button(text=label, callback_data=f"adm:inv:role:{mode}:{role}")
    b.button(text="⬅️ Назад", callback_data="adm:invite")
    b.adjust(2, 3)
    return b.as_markup()


def role_picker_kb() -> InlineKeyboardMarkup:
    """Role picker for assign via admin panel."""
    b = InlineKeyboardBuilder()
    b.button(text="🛎 Приёмка",  callback_data="adm:setrole")
    b.button(text="🛠 Инженер",  callback_data="adm:setrole")
    b.button(text="📋 Менеджер", callback_data="adm:setrole")
    b.button(text="🛡 Админ",    callback_data="adm:setrole")
    b.button(text="👑 Владелец",  callback_data="adm:setrole")
    b.adjust(2, 3)
    return b.as_markup()


def admin_settings_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📅 Рабочее окно",         callback_data="adm:set:fresh")
    b.button(text="⏰ Порог «застрял»",      callback_data="adm:set:sla")
    b.button(text="✅ Закрывающие статусы",  callback_data="adm:set:finished")
    b.button(text="🚫 Авто-статус отказа",   callback_data="adm:set:nrep")
    b.button(text="💰 Маркер «платный»",     callback_data="adm:set:paid")
    b.button(text="⬅️ Назад", callback_data="adm:back")
    b.adjust(1)
    return b.as_markup()


def auto_no_repair_kb(statuses: list[dict], selected: int | None) -> InlineKeyboardMarkup:
    """One-of picker: which RO status to auto-set on ASBIS/IT4 reject."""
    b = InlineKeyboardBuilder()
    b.button(text=("✅ " if selected is None else "  ") + "— не назначен —",
             callback_data="adm:nrep:set:0")
    for r in statuses:
        rid = int(r.get("id"))
        mark = "✅" if rid == selected else "  "
        b.button(text=f"{mark} {r.get('name')} · g{r.get('group')}",
                 callback_data=f"adm:nrep:set:{rid}")
    b.button(text="⬅️ Назад", callback_data="adm:settings")
    sizes = [1] * (len(statuses) + 1) + [1]
    b.adjust(*sizes)
    return b.as_markup()


def confirm_kb(confirm_cb: str, cancel_cb: str = "adm:back",
               confirm_label: str = "✅ Да, удалить",
               cancel_label: str = "⬅️ Отмена") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=confirm_label, callback_data=confirm_cb)
    b.button(text=cancel_label,  callback_data=cancel_cb)
    b.adjust(1, 1)
    return b.as_markup()


def back_kb(target: str = "adm:back", label: str = "⬅️ Назад") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=label, callback_data=target)
    return b.as_markup()


def finished_toggle_kb(statuses: list[dict], selected: set[int]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for r in statuses:
        rid = int(r.get("id"))
        mark = "✅" if rid in selected else "⬜"
        b.button(text=f"{mark} {r.get('name')} · g{r.get('group')}",
                 callback_data=f"adm:fin:tg:{rid}")
    b.button(text="⬅️ Назад", callback_data="adm:settings")
    sizes = [1] * len(statuses) + [1]
    b.adjust(*sizes)
    return b.as_markup()


def role_pick_kb(tg_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for role, label in [
        (Role.reception, "🛎 Приёмка"),
        (Role.engineer, "🛠 Инженер"),
        (Role.manager, "📋 Менеджер"),
        (Role.owner, "👑 Владелец"),
        (Role.admin, "🛡 Админ"),
    ]:
        b.button(text=label, callback_data=f"adm:role:{tg_id}:{role.value}")
    b.button(text="🚫 Отклонить", callback_data=f"adm:role:{tg_id}:deny")
    b.adjust(2, 2, 2)
    return b.as_markup()
