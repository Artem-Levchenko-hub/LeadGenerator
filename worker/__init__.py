"""Stenvik agent studio worker.

Отдельный процесс (systemd-юнит `stenvik-worker.service`), который:
- Запускает APScheduler с тиками orchestrator/outbox.flush_due/inbox.poll
- Берёт задачи из таблицы agent_tasks и запускает соответствующих агентов
- Пишет лог запусков (с токенами/стоимостью) в agent_runs
- Никогда не отправляет напрямую — только через outbox с холодильником

Запуск:
    python -m worker.main
"""
