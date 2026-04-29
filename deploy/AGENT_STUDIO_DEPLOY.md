# Деплой агентной студии на VPS

> Этот документ — runbook для развёртывания обновления (Спринт 1: Outreach Agent + Auditor + outbox).
> Применяется поверх существующего `lead_pipeline` (FastAPI + дашборд).

## Предусловия

- VPS `170.168.72.200` (`i48ptgvnis`), путь `/home/i48ptgvnis/stenvik-leads/`.
- Существующий `stenvik-web.service` уже работает (FastAPI-дашборд).
- Доступ по SSH (ключ или пароль).

## Шаги

### 1) Получить новый код

```bash
cd /home/i48ptgvnis/stenvik-leads
git pull origin main
```

### 2) Обновить зависимости

```bash
.venv/bin/pip install -r requirements.txt
```

Особое внимание:
- `httpx[socks]>=0.27.0` — нужно для прокси VPS→Anthropic.
- `anthropic>=0.40.0` — SDK с поддержкой `base_url` и `proxy`.

### 3) Расширить `.env`

Добавить (если ещё нет) к существующему `.env`:

```
# === Anthropic ===
ANTHROPIC_API_KEY=sk-hub-...     # ключ прокси-провайдера ИЛИ нативный sk-ant-...
ANTHROPIC_BASE_URL=https://...   # эндпоинт прокси-провайдера (если используется)
HTTP_PROXY_URL=http://user:pass@host:port  # HTTPS/SOCKS5 прокси для VPS-RU
MODEL_DEFAULT=claude-sonnet-4-6
MODEL_PREMIUM=claude-opus-4-7
DAILY_LLM_BUDGET_USD=20

# === SMTP (UniSender или любой) ===
SMTP_HOST=smtp.unisender.com
SMTP_PORT=465
SMTP_USER=outreach@stenvik.studio
SMTP_PASSWORD=...
SMTP_FROM_EMAIL=outreach@stenvik.studio
SMTP_FROM_NAME=Stenvik

# === Лимиты и холодильник ===
DAILY_EMAIL_LIMIT=30
OUTBOX_HOLDING_SECONDS=600
```

`chmod 600 .env`.

### 4) Прогнать миграцию v2

```bash
.venv/bin/python -m app.migrate
```

Должно вывести:
```
=== Migration v1 report ===
=== Migration v2 (agent studio) report ===
  seeded_kill_switch: True
  seeded_cases: ['kanavto.ru', 'kamelia', 'innertalk.space']
[ok] Миграция v2 применена.
```

Идемпотентно — можно запускать повторно.

### 5) Установить systemd-юнит воркера

```bash
sudo cp deploy/stenvik-worker.service /etc/systemd/system/stenvik-worker.service
sudo systemctl daemon-reload
sudo systemctl enable stenvik-worker
sudo systemctl start stenvik-worker
```

Проверка:
```bash
systemctl status stenvik-worker
journalctl -u stenvik-worker -f
```

В логах должно появиться:
```
Stenvik worker starting...
Worker is now running. Ctrl+C to stop.
orchestrator.tick: {'enqueued_first_touch': 0, 'enqueued_continue': 0}
```

### 6) Smoke-test (без реальной отправки писем)

#### a) Проверить kill_switch + лимиты в БД
```bash
sqlite3 data/leads.db "SELECT * FROM kill_switch;"
sqlite3 data/leads.db "SELECT name, restrictions_text FROM cases WHERE name='innertalk.space';"
```

Должно показать строку с `state=running` и кейс innertalk с явным запретом упоминать шифрование.

#### b) Проверить что Anthropic API доступен через прокси
```bash
.venv/bin/python -c "
from worker.llm import get_anthropic_client
c = get_anthropic_client()
r = c.messages.create(
    model='claude-sonnet-4-6', max_tokens=20,
    messages=[{'role':'user','content':'say hi'}]
)
print(r.content[0].text)
"
```

Если падает с `Connection refused` или `timeout` — проверь `HTTP_PROXY_URL` в `.env`.

#### c) Smoke innertalk-guard
```bash
.venv/bin/python -c "
from app.database import SessionLocal
from app import models
from worker.auditor import audit
import secrets

with SessionLocal() as db:
    msg = models.OutboxMessage(
        company_id=None, channel='email', to_address='test@example.com',
        body_text='Расскажу про innertalk.space — корпоративный мессенджер с зашифрованными чатами. stenvik.studio. Unsubscribe.',
        status='draft', recall_token=secrets.token_urlsafe(16),
    )
    print(audit(db, msg))  # должно: rejected, rule=innertalk_no_encryption
"
```

### 7) Откат

```bash
sudo systemctl stop stenvik-worker
sudo systemctl disable stenvik-worker
git revert HEAD
```

БД новые таблицы остаются — это безопасно, существующее приложение их игнорирует.

## Что дальше (Спринт 2+)

- Inbound: IMAP-poller для ответов на холодные.
- Telegram-бот.
- Sales/Discovery/Requirements агенты.
- Dashboard страницы `/control`, `/companies`, `/feed`.
