# Testing & Iteration Workflow

## Auto-Research: Continuous Health Check

Используйте `research.py` для автоматического мониторинга здоровья системы:

```bash
# Полный анализ (ui_walk + e2e_probe + integration_probe + детектив-проверки)
python scripts\research.py

# Быстрый прогон (только ui_walk + детектив)
python scripts\research.py --quick
```

**Что проверяется:**

1. **Probes** — все 3 теста должны пройти ✓
   - `ui_walk` — headless клики по всем кнопкам, проверка текстов и кнопок
   - `e2e_probe` — рендер экранов + симуляция логики ролей
   - `integration_probe` — live e2e: бот + группа + RemOnline

2. **Detective checks** — мета-проверки кода:
   - Все хендлеры импортируются ✓
   - Тексты (HELLO, ROLE_TIPS, REQUEST_LABELS) на месте ✓
   - БД модели OK ✓
   - RemOnline client OK ✓
   - Config с токеном и API-ключом ✓

3. **Issues** — агрегация ошибок и варнингов
4. **Recommendations** — авто-предложения по фиксам

## Workflow: Find → Report → Fix → Iterate

Когда `research.py` выявит проблему:

### 1. Читайте отчёт внимательно

```
❌ RESEARCH FAILED — issues to address

⚠️ ISSUES:
   ✗ ERROR:
      • regression: Failed probes: e2e_probe
      • e2e_failure: e2e_probe: 3/85 tests failed

   ⚠ WARNING:
      • slow: ui_walk: 120.5s
```

### 2. Скажите, что нужно найти/поправить

**Примеры:**

- "найди почему e2e_probe падает на тесте #42"
- "ui_walk медленный, оптимизируй"
- "integration_probe время ожидания истекло"
- "менеджер не может отклонить запрос — проверь логику"

### 3. Я диагностирую и фиксу

Запущу дополнительные проверки, посмотрю код, найду bug, поправлю.

### 4. Прогоняю research.py снова

Проверяем, что fix сработал и ничего другого не сломали.

### 5. Повторяем N раз до идеала

Каждый раунд "research → report → fix" доводит бот к состоянию "✅ RESEARCH OK".

## Примеры проблем и фиксов

| Проблема | Команда | Результат |
| --- | --- | --- |
| Кнопка не работает | "клик на 👤 Взять не берёт заказ" | Проверю cb_queue_take, может быть проблема с DB или FSM |
| Текст странный | "в очереди ошибка в тексте" | Найду ошибку в views.py или texts.py, поправлю |
| Падает тест | "e2e_probe test #15 падает" | Посмотрю скрипт на строке 15, найду баг в логике |
| Медленно | "integration_probe время ожидания" | Добавлю timeout, оптимизирую polling |

## Scripts Reference

### Основные
- `ui_walk.py` — headless, нет сети. 31 checks. Быстро (50-60s).
- `e2e_probe.py` — offline simulation. 85 tests. ~20s.
- `integration_probe.py` — live! Бот + RemOnline + группа. ~10s.
- `research.py` — orchestrator. Запускает все + detective, выдаёт report.

### Утилиты (в `attic/`)
- `seed_ro_orders.py` — создать тестовые заказы
- `sim_week.py` — симуляция недельного цикла
- `integration_probe.py` — live тестирование

## Быстрый старт

1. **Первый раз:**
   ```bash
   python scripts\research.py
   ```
   Должно быть: `✅ RESEARCH OK`

2. **После каждого изменения кода:**
   ```bash
   python scripts\research.py --quick
   ```
   Проверяем что не сломали.

3. **Перед сессией тестирования в Telegram:**
   ```bash
   python scripts\research.py
   ```
   Полный прогон всех проб.

4. **Если что-то упало:**
   ```
   ❌ RESEARCH FAILED
   
   [сообщаешь пользователю что сломалось]
   
   [пользователь говорит что фиксить]
   
   [я фиксу]
   
   python scripts\research.py  # check fix
   ```

## Signal Quality

После каждого `research.py` ты получаешь одно из:

- ✅ **OK** — готово к Telegram-тестированию
- ⚠️ **OK с варнингами** — работает, но есть вещи помедленнее или логи
- ❌ **FAIL** — есть ошибки, нужны фиксы
