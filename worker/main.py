"""Worker entrypoint — APScheduler runner.

Запуск:
    python -m worker.main

Производственный запуск через systemd-юнит `stenvik-worker.service`.
"""
from __future__ import annotations

import logging
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from worker import orchestrator
from worker import dispatcher
from worker import outbox_flush
from worker.inbound import imap_poller
from worker.hunter import main as hunter_main
from worker.agents import collector as observer_collector


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("worker")


def _safe(fn, name: str):
    try:
        result = fn()
        log.info("%s: %s", name, result)
    except Exception:  # noqa: BLE001
        log.exception("%s failed", name)


def main() -> int:
    log.info("Stenvik worker starting...")
    sched = BlockingScheduler(timezone="UTC")

    # Tactical Orchestrator — каждую минуту.
    sched.add_job(
        lambda: _safe(orchestrator.tick, "orchestrator.tick"),
        CronTrigger.from_crontab("* * * * *"),
        id="orchestrator_tick", max_instances=1, coalesce=True,
    )

    # Dispatcher — каждые 60 секунд.
    sched.add_job(
        lambda: _safe(dispatcher.dispatch, "dispatcher.dispatch"),
        CronTrigger.from_crontab("* * * * *"),
        id="dispatcher", max_instances=1, coalesce=True,
    )

    # Outbox flush (drafts + due) — каждую минуту.
    sched.add_job(
        lambda: _safe(outbox_flush.flush_all, "outbox.flush_all"),
        CronTrigger.from_crontab("* * * * *"),
        id="outbox_flush", max_instances=1, coalesce=True,
    )

    # IMAP poll входящих — каждые 5 минут.
    sched.add_job(
        lambda: _safe(imap_poller.poll_inbox, "inbound.imap_poll"),
        CronTrigger.from_crontab("*/5 * * * *"),
        id="imap_poll", max_instances=1, coalesce=True,
    )

    # Hunter — каждые 20 минут, до 10 новых лидов за тик
    # (≈30 тиков/день × 10 = 300 теоретических, реально с дедупом ≈100-150).
    sched.add_job(
        lambda: _safe(lambda: hunter_main.run_one_tick(max_per_tick=10), "hunter.tick"),
        CronTrigger.from_crontab("*/20 * * * *"),
        id="hunter_tick", max_instances=1, coalesce=True,
    )

    # Observer Collector — каждый час в :05, DeepSeek пишет короткий
    # snapshot/аномалию в `observations`. CEO (Opus 4.7) на manual daily
    # audit'е читает 24 последних observation'а как «память дня».
    sched.add_job(
        lambda: _safe(observer_collector.collect_one_hour, "collector.hourly"),
        CronTrigger.from_crontab("5 * * * *"),
        id="observer_collector", max_instances=1, coalesce=True,
    )

    def shutdown(signum, _frame):  # type: ignore[no-untyped-def]
        log.info("signal %s received, shutting down", signum)
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info("Worker is now running. Ctrl+C to stop.")
    sched.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
