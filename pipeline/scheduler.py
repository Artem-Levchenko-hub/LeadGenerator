import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from pipeline.runner import run_pipeline_once

logger = logging.getLogger(__name__)


def _scheduled_job():
    logger.info("Starting scheduled pipeline run")
    try:
        result = run_pipeline_once()
        logger.info("Pipeline run result: %s", result)
    except Exception:
        logger.exception("Scheduled pipeline failed")


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=settings.tz)
    scheduler.add_job(
        _scheduled_job,
        trigger="interval",
        minutes=settings.pipeline_interval_minutes,
        id="lead_pipeline",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: every %s min", settings.pipeline_interval_minutes
    )
    return scheduler
