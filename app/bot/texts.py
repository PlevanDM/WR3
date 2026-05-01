"""Single source of truth for user-facing strings.

Keep the tone warm, short and uniform across DM and gallery. No tech jargon.
"""
from __future__ import annotations


# ---- brand / core ----
BRAND = "Warranty"

HELLO = f"👋 <b>{BRAND}</b> — сервисный помощник для заказов"

NO_ACCESS = (
    "🔒 Пока нет доступа.\n"
    "Попроси администратора выдать тебе роль."
)
ACCESS_REQUESTED = "✉️ Заявка отправлена — админ получит уведомление."

UNKNOWN = "Не понял. Напиши /start — открою меню."
CANCELLED = "❎ Отменено."


# ---- roles ----
ROLE_LABELS = {
    "admin":     "Админ",
    "owner":     "Владелец",
    "reception": "Приёмка",
    "engineer":  "Инженер",
    "manager":   "Менеджер",
}

ROLE_ICONS = {
    "admin":     "🛡",
    "owner":     "👑",
    "reception": "🛎",
    "engineer":  "🛠",
    "manager":   "📋",
}

# Short, product-style brief per role — no menu reference, no button list.
ROLE_TIPS = {
    "admin": (
        "Ты видишь систему целиком: пользователей, настройки, очередь, запросы. "
        "Можешь назначать инженеров, поднимать приоритет, чистить галерею."
    ),
    "owner": (
        "Ты имеешь доступ только на чтение: виджеты, статистика и наблюдение. "
        "Рабочие функции и изменение заказов отключены, чтобы не сбить процесс."
    ),
    "reception": (
        "Принимаешь устройства, снимаешь фото и следишь за тем, чтобы у каждого "
        "заказа они были. Если что-то давно не двигается — увидишь это в разделе "
        "«Застряли»."
    ),
    "engineer": (
        "Берёшь заказ из очереди, работаешь, при необходимости шлёшь запрос "
        "менеджеру: Асбис, ИТ4 или свободным текстом. Ответ прилетает сюда же."
    ),
    "manager": (
        "Обрабатываешь запросы инженеров: подтверждаешь, отправляешь в работу "
        "или отклоняешь с причиной. Все действия автоматически остаются "
        "комментарием в RemOnline."
    ),
}


# ---- requests ----
REQUEST_LABELS = {
    "asbis":     "Асбис",
    "it4":       "ИТ4",
    "custom":    "Другое",
    "outsource": "Аутсорс",
}


# ---- repair kind (warranty vs paid) ----
KIND_LABELS = {
    True:  "💰 Платный",
    False: "🛡 Гарантия",
}


def kind_badge(is_paid: bool | None) -> str:
    return "💰" if is_paid else "🛡"

REQUEST_STATUS_LABELS = {
    "open":        "🕘 открыт",
    "in_progress": "⚙️ в работе",
    "done":        "✅ готово",
    "rejected":    "🚫 отклонён",
}


def human_request_status(
    rtype: str | None, status: str, note: str | None = None,
) -> str:
    """Context-aware label for a request status line in DM / gallery."""
    note = (note or "").strip()
    if status == "rejected" and note == "cancelled_by_author":
        return "↩️ отменён автором"
    if status == "open":
        return "🕘 ждёт менеджера"
    if status == "in_progress":
        return "⚙️ менеджер взял в работу"
    if status == "done":
        if rtype == "asbis":
            return "✅ принят в обслуживание"
        if rtype == "it4":
            return "✅ оформление идёт"
        return "✅ готово"
    if status == "rejected":
        return "🚫 отклонён менеджером"
    return REQUEST_STATUS_LABELS.get(status, status)


REQ_PROMPT = {
    "custom": (
        "✏️ <b>Сообщение менеджеру</b>\n"
        "Опиши ситуацию своими словами — можно короткий комментарий или вопрос. "
        "Или нажми «➡️ Без текста».\n\n"
        "/cancel — отмена."
    ),
    "outsource": (
        "🌐 <b>Передать на аутсорс</b>\n"
        "Напиши кому передаём и причину — текст обязателен. "
        "Менеджер увидит этот комментарий и продублирует его в RemOnline.\n\n"
        "/cancel — отмена."
    ),
}


# ---- events (for dashboards / digests) ----
EVENTS = {
    "order_created":        "🆕 Новый заказ <b>#{number}</b>",
    "order_status_changed": "🔄 <b>#{number}</b>: статус → <i>{status}</i>",
    "photos_added":         "📷 <b>#{number}</b>: +{count} фото",
    "order_taken":          "👤 {engineer} взял <b>#{number}</b>",
    "request_created":      "📨 [{type}] по <b>#{number}</b>\n<i>{payload}</i>",
    "request_status":       "📬 Запрос #{rid} по <b>#{number}</b>: {status}{note_line}",
    "queue_moved":          "🔝 <b>#{number}</b> → позиция <b>{pos}</b>",
}


# ---- order status emoji mapping ----
def status_emoji(status_name: str | None) -> str:
    if not status_name:
        return "🏷"
    s = status_name.lower()
    if any(k in s for k in ("выдан", "закрыт", "завершен", "завершён", "готов")):
        return "🟢"
    if any(k in s for k in ("отказ", "отмен")):
        return "🔚"
    if any(k in s for k in ("ремонт", "в работе", "диагност")):
        return "🔧"
    if any(k in s for k in ("приня", "новый", "создан")):
        return "🆕"
    return "🏷"
