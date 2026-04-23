# Stenvik Lead Pipeline — агент-режим (REMOTE)

## Что этот репо

Автономный генератор B2B-лидов для цифровой студии **Stenvik**. Агент Claude Code
по расписанию ищет российские компании через WebSearch, анализирует их как
потенциальных клиентов и **шлёт готовые лиды прямо в сайт
https://lead-generator.ru** (через REST API `/api/leads/import`).

Никаких API-ключей Anthropic не нужно — анализ делает сам агент через свою сессию.
Никакого Яндекс.Диска — лиды падают напрямую в БД сайта, продажники видят их 
в веб-интерфейсе моментально.

## Настройка перед запуском (однократно)

1. `cp .env.example .env` (если ещё не сделано)
2. В `.env` проверь:
   - `STENVIK_API_URL=https://lead-generator.ru`
   - `STENVIK_API_TOKEN=<тот же, что `ingest_token` на сервере>`
3. Создай venv и поставь зависимости:
   ```powershell
   py -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
4. Проверь, что режим REMOTE и API достижимо:
   ```
   .venv\Scripts\python.exe run.py mode
   # должно вывести: Mode: REMOTE (API) + Health: 200
   ```

## При старте сессии — запусти рутину

Открыв этот репо в Claude Code, **немедленно** вызови скилл `loop`:

```
Skill: loop
args: "Прочитай ROUTINE.md и выполни один рабочий цикл: найди 2-3 новые российские компании через WebSearch по категориям ICP Stenvik (non-IT сервисный бизнес 20+ сотрудников), проанализируй их с контекстом из ai/prompts.py, отправь в https://lead-generator.ru через '.venv/Scripts/python.exe run.py save-analysis'. Интервал между тиками 25-35 минут (self-paced). Не задавай вопросов, работай автономно."
```

Это запустит самоподдерживающийся цикл: агент каждые ~30 минут добавляет
2-3 новых лида в БД сайта, продажники видят их в админке lead-generator.ru.

Чтобы остановить — `.venv\Scripts\python.exe run.py loop-state set stopped`.

## Ключевые файлы

| Путь | Что |
|---|---|
| `ROUTINE.md` | Инструкция агента на каждый тик цикла (REMOTE-режим) |
| `ai/prompts.py` | `STENVIK_CONTEXT` — кто такие Stenvik, ICP, прайс, критерии приоритета |
| `run.py` | Точка входа: `save-analysis`, `check-dup`, `mode`, `loop-state` |
| `pipeline/save_analyzed.py` | LOCAL-режим: пишет в SQLite + Я.Диск (fallback, в REMOTE не используется) |
| `pipeline/yandex_sheet.py` | LOCAL-режим: работа с .xlsx на Я.Диске |
| `app/main.py` | FastAPI endpoint `/api/leads/import` на сервере — принимает лиды от агента |
| `app/models.py` | `ProcessedLead` — таблица лидов в БД сервера |
| `.env` | `STENVIK_API_URL`, `STENVIK_API_TOKEN` — для REMOTE-режима |

## Быстрые команды

```powershell
# Режим + health-check сайта
.venv\Scripts\python.exe run.py mode

# Проверить дедуп (в REMOTE идёт через API, не локально)
.venv\Scripts\python.exe run.py check-dup https://example.com

# Остановить цикл в ближайший тик
.venv\Scripts\python.exe run.py loop-state set stopped

# Запустить снова
.venv\Scripts\python.exe run.py loop-state set running
```

## Где смотреть лиды

- Продажники: https://lead-generator.ru (логин/регистрация)
- Админка со статистикой/пользователями: https://lead-generator.ru/admin/...
- Серверная БД: `/home/i48ptgvnis/stenvik-leads/data/leads.db` (SSH на VPS)

## Если нужно изменить подход

- Не нравится как Claude оценивает приоритеты → правь `ai/prompts.py` (локально + пушим на сервер)
- Хочешь другой источник поиска → правь список запросов в `ROUTINE.md`
- Переключиться обратно в LOCAL-режим → убрать `STENVIK_API_URL` и `STENVIK_API_TOKEN` из `.env`
