# Stenvik Lead Pipeline — агент-режим

## Что этот репо

Автономный генератор B2B-лидов для цифровой студии **Stenvik**. Агент Claude Code
по расписанию ищет российские компании через WebSearch, анализирует их как
потенциальных клиентов и пишет в Яндекс.Таблицу `/Stenvik/leads.xlsx` (на
Я.Диске владельца).

**Никаких API-ключей Anthropic.** Анализ делает сам агент через свою модель.

## При старте сессии — запусти рутину

Если тебя открыли в этом репо, **немедленно** вызови скилл `loop` с инструкциями
из `ROUTINE.md`:

```
Skill: loop
args: "Прочитай C:/Лидогенератор/repo/ROUTINE.md и выполни один рабочий цикл: найди 3-5 новых российских компаний через WebSearch, проанализируй их с учётом контекста из ai/prompts.py, сохрани в Яндекс.Таблицу через 'py run.py save-analysis'. Интервал между тиками — 20-30 минут (self-paced)."
```

Это запустит самоподдерживающийся цикл: агент каждые ~25 минут будет добавлять
3-5 новых лидов в таблицу.

## Ключевые файлы

| Путь | Что |
|---|---|
| `ROUTINE.md` | Инструкция агента на каждый тик цикла |
| `ai/prompts.py` | `STENVIK_CONTEXT` — кто такие Stenvik, ICP, прайс, критерии приоритета |
| `pipeline/yandex_sheet.py` | Работа с .xlsx на Я.Диске (download/append/upload/publish) |
| `pipeline/save_analyzed.py` | CLI: принимает JSON на stdin, пишет строку в таблицу |
| `run.py` | Точка входа: `save-analysis`, `check-dup`, `stats`, `recent` |
| `app/models.py` | SQLite `ProcessedLead` (дедуп) + `RunLog` |
| `.env` | `YANDEX_DISK_TOKEN` — OAuth-токен для Я.Диска |

## Быстрые команды

```bash
cd C:/Лидогенератор/repo

# Сводка: сколько лидов собрано, распределение по приоритетам
.venv/Scripts/python.exe run.py stats

# Последние 20 обработанных
.venv/Scripts/python.exe run.py recent 20

# Проверить, есть ли компания уже в базе
.venv/Scripts/python.exe run.py check-dup https://example.com
```

## Если нужно изменить подход

- Не нравится как Claude оценивает приоритеты → правь `ai/prompts.py`
- Хочешь другой источник поиска → правь список запросов в `ROUTINE.md`
- Добавить колонки в таблицу → правь `COLUMNS` в `pipeline/yandex_sheet.py`
  (существующая таблица останется без новой колонки, пересоздай файл)
