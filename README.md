# Stenvik Lead Pipeline

Автоматический генератор B2B-лидов для цифровой студии **Stenvik** (stenvik.studio).
Собирает компании с HH.ru → проверяет их сайты → **Claude Sonnet 4.6** анализирует каждую
как потенциального клиента → выдаёт продажникам таблицу с приоритетом и персональным хуком.

> **Репо:** https://github.com/Artem-Levchenko-hub/LeadGenerator
> **Цель:** 3-4 горячих лида в час, ~30-50 в день, без ручного труда.

---

## 📋 Что делает

1. **Берёт работодателей с HH.ru** по городам (Москва, СПб, Екб, НСК, Казань, и т.п.) через публичное API.
2. **Отсеивает IT-компании** (у них свои разработчики, они не ICP Stenvik).
3. **Пытается открыть сайт** каждой компании:
   - Определяет CMS/стек (WordPress, Tilda, Bitrix, Joomla и т.п.)
   - Проверяет наличие HTTPS, заголовки, description
   - Качает ~6KB чистого текста с главной страницы
4. **Отправляет в Claude Sonnet 4.6** всё, что знает о компании + системный промпт с описанием
   услуг Stenvik (из `ai/prompts.py`). Claude возвращает:
   - Краткую сводку о компании
   - 3-5 **конкретных болей**
   - 1-3 **рекомендованных услуги Stenvik** из прайса
   - **Персонализированный хук** для первого звонка продажника
   - Приоритет 1-5 + обоснование
5. **Сохраняет в SQLite**, показывает в веб-интерфейсе (FastAPI + Bootstrap).

---

## 🚦 Статус проекта (что готово / что дальше)

| Блок | Статус | Где в коде |
|---|---|---|
| Структура проекта + конфиги | ✅ | `app/config.py`, `.env.example` |
| SQLAlchemy модели (Lead, RunLog) | ✅ | `app/models.py` |
| HH.ru скрейпер | ✅ | `scrapers/hh_employers.py` |
| Website скрейпер + детектор стека | ✅ | `scrapers/website_scraper.py` |
| Claude Sonnet анализатор + prompt caching | ✅ | `ai/analyzer.py`, `ai/prompts.py` |
| Оркестратор пайплайна + APScheduler | ✅ | `pipeline/runner.py`, `pipeline/scheduler.py` |
| Ручная команда анализа одного URL/компании | ✅ | `pipeline/analyze_one.py` |
| FastAPI веб-интерфейс (dashboard, лиды, фильтры, CRM-статусы) | ✅ | `app/main.py`, `app/templates/` |
| HTTP Basic Auth (до 5 продажников) | ✅ | `app/auth.py` |
| Деплой-конфиги для VPS (nginx + gunicorn + systemd) | ✅ | `deploy/` |
| **Smoke-тест локально** | ⏳ | — (нужен API-ключ в `.env`) |
| **Деплой на VPS** | ⏳ | инструкция в [deploy/DEPLOY.md](deploy/DEPLOY.md) |
| **Тюнинг промпта** (после первых реальных лидов) | ⏳ | `ai/prompts.py` |

---

## 🚀 Quickstart для нового человека

### 1. Склонировать репо

```bash
git clone https://github.com/Artem-Levchenko-hub/LeadGenerator.git
cd LeadGenerator
```

### 2. Получить Anthropic API-ключ

1. Зайти на [console.anthropic.com](https://console.anthropic.com) (можно под тем же аккаунтом, что claude.ai).
2. Billing → пополнить на **$5-10** (хватит на 2-4 месяца работы нашего объёма).
3. API Keys → **Create Key** → скопировать `sk-ant-...`.

> ⚠️ **Важно про MAX-подписку:** даже если есть Claude MAX, API — отдельный продукт
> с отдельным биллингом. MAX покрывает чат на claude.ai и Claude Code CLI, но НЕ API.
> Для автоматизации нужен именно API-ключ.

### 3. Настроить окружение

```powershell
# Windows:
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Linux / Mac:
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Настроить .env

```powershell
copy .env.example .env     # Windows
# или
cp .env.example .env       # Linux/Mac
```

Отредактировать `.env`:
```
ANTHROPIC_API_KEY=sk-ant-твой-ключ
APP_SECRET=любая-длинная-случайная-строка
AUTH_USERS=admin:твой-пароль,sales1:другой-пароль
```

### 5. Быстрые тесты

```powershell
# Тест №1: анализ одного сайта (~15 сек)
py run.py analyze https://stenvik.studio

# Тест №2: поиск компании на HH + анализ
py run.py analyze "Ромашка"

# Тест №3: пайплайн на 3 лидах (~2-3 мин)
py run.py pipeline 3
```

После теста №3 в `data/leads.db` появятся первые лиды.

### 6. Запуск веб-интерфейса

```powershell
py run.py
```

Открыть [http://127.0.0.1:8000](http://127.0.0.1:8000), войти под `admin`.

> При первом запуске автоматически стартует APScheduler — пайплайн будет сам
> дёргаться каждые 15 минут (настройка в `.env`, `PIPELINE_INTERVAL_MINUTES`).

---

## 🏗️ Архитектура

```
┌─────────────────────┐
│   APScheduler       │ каждые 15 мин
│   (inside FastAPI)  │
└──────────┬──────────┘
           ▼
┌─────────────────────────────────────────────────────────┐
│   pipeline/runner.py — оркестратор                     │
│                                                         │
│   для каждого города из HH_CITIES:                     │
│     HH.ru API → employers list (100/page)              │
│       для каждого employer:                            │
│         1. уже в БД? → skip                            │
│         2. IT-индустрия? → skip                        │
│         3. fetch_site(employer.site_url)               │
│         4. Claude Sonnet 4.6 анализирует               │
│         5. save Lead в SQLite                          │
└─────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────┐         ┌──────────────────────┐
│  data/leads.db      │ ◄─────► │  FastAPI Web UI      │
│  (SQLite)           │         │  /, /leads, /leads/{id} │
│                     │         │  HTTP Basic Auth     │
│  Table: leads       │         │  Bootstrap 5         │
│  Table: run_logs    │         └──────────────────────┘
└─────────────────────┘
```

**Файлы по ролям:**

| Что | Где |
|---|---|
| Конфиг из .env | `app/config.py` |
| Модели БД | `app/models.py` |
| Подключение к БД | `app/database.py` |
| HTTP маршруты FastAPI | `app/main.py` |
| Аутентификация | `app/auth.py` |
| HTML-шаблоны | `app/templates/*.html` |
| HH.ru API-клиент | `scrapers/hh_employers.py` |
| Фетч и парсинг сайта | `scrapers/website_scraper.py` |
| **Промпт для Claude** (тюнинг здесь!) | `ai/prompts.py` |
| Вызов Claude API | `ai/analyzer.py` |
| Оркестратор | `pipeline/runner.py` |
| Ручной анализ одного URL | `pipeline/analyze_one.py` |
| APScheduler | `pipeline/scheduler.py` |
| Точка входа CLI/веб | `run.py` |
| VPS деплой | `deploy/` |

---

## ⚙️ Конфигурация (`.env`)

| Переменная | Что | По умолчанию |
|---|---|---|
| `ANTHROPIC_API_KEY` | Ключ API Anthropic (обязательно!) | — |
| `APP_SECRET` | Секрет для сессий | — |
| `DATABASE_URL` | URL БД | `sqlite:///./data/leads.db` |
| `AUTH_USERS` | Логины:пароли через запятую | `admin:admin123` |
| `HH_CITIES` | ID городов HH через запятую | `1,2,3,4,38,113` |
| `HH_EXCLUDE_INDUSTRIES` | ID индустрий HH для исключения | `7` (IT) |
| `PIPELINE_INTERVAL_MINUTES` | Интервал запуска пайплайна | `15` |
| `LEADS_PER_RUN` | Сколько лидов добавляется за прогон | `10` |
| `CLAUDE_MODEL` | Модель Claude | `claude-sonnet-4-6` |
| `TZ` | Таймзона для планировщика | `Europe/Moscow` |

**Справочник city ID HH.ru:** 1=Москва, 2=СПб, 3=Екб, 4=Новосибирск, 66=Нижний Новгород, 88=Казань, 53=Краснодар, 76=Ростов-на-Дону.

---

## 💰 Стоимость

| Компонент | Цена |
|---|---|
| HH.ru API | Бесплатно |
| Claude Sonnet 4.6 с prompt caching | ~**2₽ на лид** |
| Парсинг сайтов | Бесплатно |
| VPS (Timeweb/Reg.ru/Selectel) | ~250-500 ₽/мес |
| **Итого при 30-50 лидов/день** | ~**300-500 ₽/мес** полностью |

---

## 🖥️ Деплой на VPS

Полная пошаговая инструкция → **[deploy/DEPLOY.md](deploy/DEPLOY.md)**

Общие шаги (Ubuntu 22.04):
```bash
git clone https://github.com/Artem-Levchenko-hub/LeadGenerator.git /opt/lead_pipeline
cd /opt/lead_pipeline
# → python-venv, pip install, настроить .env, systemd, nginx, Let's Encrypt
```

После деплоя и правок кода:
```bash
cd /opt/lead_pipeline && git pull && systemctl restart lead-pipeline
```

---

## 🔧 Тюнинг и кастомизация

### Плохие приоритеты / хуки
Правь `ai/prompts.py` — там системный промпт. Описание Stenvik, ICP, правила
приоритизации — всё здесь. После правки запускай `py run.py analyze <url>` на
тестовых сайтах, смотри выдачу.

### Добавить город
В `.env` → `HH_CITIES=1,2,3,4,66`. Названия и ID — см. `scrapers/hh_employers.py::AREA_NAMES`.

### Добавить индустрию для исключения
В `.env` → `HH_EXCLUDE_INDUSTRIES=7,116`. ID индустрий — [справочник HH](https://api.hh.ru/industries).

### Изменить формат выхода Claude
`ai/analyzer.py::LeadAnalysis` — Pydantic-модель. Меняешь поля — меняется JSON.
Не забудь синхронизировать с `app/models.py::Lead` и шаблонами `app/templates/*.html`.

### Сменить Claude на более дешёвую Haiku
В `.env`: `CLAUDE_MODEL=claude-haiku-4-5`. Стоимость падает в ~3 раза, качество
анализа заметно проще — не рекомендуется для продакшена, но ок для экспериментов.

---

## 🐛 Troubleshooting

**`pip install` падает на `lxml` / `pydantic-core`**
Вероятно, Python 3.14 слишком свежий — не все wheel-ы готовы. Поставь Python 3.12 параллельно,
создай venv на нём: `py -3.12 -m venv .venv`.

**`ImportError: cannot import name 'xxx' from 'anthropic'`**
Обнови SDK: `pip install -U anthropic>=0.100.0`. `messages.parse()` появился в свежих версиях.

**Пайплайн ничего не добавляет — в `run_logs` `errors > 0`**
Смотри `details` в `run_logs` (или логи веба). Часто это:
- Таймауты на медленных сайтах → увеличь `timeout` в `scrapers/website_scraper.py::fetch_site`
- HH rate limit → увеличь `sleep_between` в `iter_employers_for_area`

**Claude говорит "AuthenticationError"**
Ключ в `.env` неправильный / не пополнен баланс. Проверь на [console.anthropic.com](https://console.anthropic.com).

**Веб открывается, но `/` ругается 500**
Смотри консоль, где `py run.py`. Обычно БД не создалась — удали `data/leads.db` и перезапусти.

---

## 📝 Roadmap (что можно добавить)

- [ ] Парсинг 2GIS/Яндекс.Карт для локального бизнеса без присутствия на HH
- [ ] Обогащение контактов через Hunter.io (email по домену)
- [ ] Telegram-бот для уведомлений о горячих лидах
- [ ] Экспорт в CSV/Excel для продажников
- [ ] Переход с SQLite на PostgreSQL при росте > 50k лидов
- [ ] Интеграция с Bitrix24/amoCRM (push лидов в CRM)
- [ ] Дедупликация по ИНН (не только по HH employer_id)

---

## 📞 Контакты

**Stenvik** · [stenvik.studio](https://stenvik.studio) · hello@stenvik.studio
