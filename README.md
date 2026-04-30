# Warranty 3.0 Telegram Bot

Telegram-бот сервис-центра, интегрированный с RemOnline. Роли: admin, reception, engineer, manager. Группа Warranty 3.0 используется как фото-галерея и лента событий.

## Стек
Python 3.12 · aiogram 3 · SQLAlchemy 2 (async) · PostgreSQL 16 · Redis 7 · APScheduler · httpx · Docker Compose.

## Быстрый старт (локально на Windows, Docker Desktop)

1. Скопируй `.env.example` в `.env` и заполни:
   - `BOT_TOKEN` — из @BotFather (перевыпусти текущий, он засветился)
   - `ADMIN_TG_IDS` — твой Telegram ID (можно несколько через запятую)
   - `REMONLINE_API_KEY` — API-ключ RemOnline
   - `REMONLINE_ORDER_URL` — шаблон URL для перехода в заказ (`https://web.roapp.io/orders/{id}` или тот, что реально открывает твой заказ)

2. Запуск:
   ```
   docker compose up -d --build
   docker compose logs -f bot
   ```

3. В Telegram найди `@warranty3_bot`, отправь `/start`. Первый админ (из `ADMIN_TG_IDS`) создастся автоматически.

4. Добавь бота в группу Warranty 3.0 как админа, потом в этой группе отправь `/bind_gallery` от имени админа.

5. Назначение ролей пользователям:
   - Нажми «Админ» → «Пользователи и роли»
   - `/role <tg_id> <admin|reception|engineer|manager>`
   - `/deactivate <tg_id>` — отключить

## Команды

- `/start` · `/whoami`
- Приёмка: «Без фото», «Старше 7 дней», кнопка «Загрузить фото» на карточке
- Инженер: «Очередь» — взять в работу, запросы (Асбис/ИТ4/свой)
- Менеджер: «Входящие запросы» или `/inbox`
- Админ: «Админ» — меню, `/role`, `/deactivate`, `/sla_days <N>`, `/bind_gallery`

## Миграции

Применяются автоматически при старте контейнера. Вручную:
```
docker compose run --rm bot alembic upgrade head
```
Создать новую:
```
docker compose run --rm bot alembic revision -m "name" --autogenerate
```

## Интеграция с RemOnline

- Поллинг `GET /orders?sort=-modified_at` раз в `POLL_INTERVAL_SEC` сек.
- При появлении новых/изменённых заказов пишется событие и постится карточка в группу Warranty 3.0.
- Фото грузятся приёмкой через бота, сохраняются альбомом в группе; ссылки на TG-сообщения пишутся комментарием к заказу в RemOnline (`POST /orders/{id}/comments`).
- Кнопка «Открыть в RemOnline» ведёт по шаблону `REMONLINE_ORDER_URL`.

## Безопасность

- `.env` не коммитить (в `.gitignore`).
- Токен бота, который был в чате, перевыпустить в @BotFather перед запуском прод-режима.
- API-ключ RemOnline — отдельный на тест/прод.

## Структура

```
app/                     # код бота (handlers, services, db, remonline, config)
alembic/                 # миграции
scripts/                 # рабочие скрипты:
  seed_ro_orders.py        — создаёт тестовые заказы в RemOnline
  sim_week.py              — оффлайн симуляция недельного цикла всех ролей
  e2e_probe.py             — рендер всех экранов + клики (без сети)
  integration_probe.py     — live e2e (RemOnline + группа + бот)
  ui_walk.py               — headless «прокликать всё», читаемый отчёт
data/                    # SQLite + ран-логи
attic/                   # архив одноразовых утилит и старых логов (см. attic/README.md)
```
