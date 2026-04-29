"""Task dispatcher — берёт N pending agent_tasks и запускает соответствующего агента.

Запускается из APScheduler каждую минуту. Использует ThreadPoolExecutor
(I/O-bound: HTTP + Anthropic API).

Важно: на SQLite атомарность достигается транзакцией с UPDATE+WHERE.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable

from sqlalchemy import and_

from app import models
from app.database import SessionLocal


log = logging.getLogger(__name__)

# Маппинг task.kind → handler-функция (заполняется ниже).
_HANDLERS: dict[str, Callable[..., dict]] = {}


def register(kind: str, fn: Callable[..., dict]) -> None:
    _HANDLERS[kind] = fn


def _claim_task(db, task_id: int) -> bool:
    """Атомарно забирает задачу: status=pending → running."""
    res = db.execute(
        models.AgentTask.__table__.update()
        .where(and_(
            models.AgentTask.__table__.c.id == task_id,
            models.AgentTask.__table__.c.status == "pending",
        ))
        .values(status="running", started_at=datetime.utcnow())
    )
    db.commit()
    return res.rowcount > 0


def _finalize_task(task_id: int, success: bool, error: str | None = None) -> None:
    with SessionLocal() as db:
        t = db.query(models.AgentTask).filter_by(id=task_id).one_or_none()
        if not t:
            return
        t.status = "done" if success else "failed"
        t.finished_at = datetime.utcnow()
        if error:
            t.last_error = error[:2000]
            t.attempts = (t.attempts or 0) + 1
        db.add(t)
        db.commit()


def _process_one(task_id: int) -> None:
    with SessionLocal() as db:
        if not _claim_task(db, task_id):
            return  # кто-то другой уже забрал
        task = db.query(models.AgentTask).filter_by(id=task_id).one()
        kind = task.kind
        company_id = task.company_id
        conversation_id = task.conversation_id
        payload = task.payload or {}

    handler = _HANDLERS.get(kind)
    if handler is None:
        _finalize_task(task_id, False, f"no handler for kind {kind}")
        return

    try:
        kwargs = {"task_id": task_id}
        if "company_id" in payload:
            kwargs["company_id"] = payload["company_id"]
        elif company_id is not None:
            kwargs["company_id"] = company_id
        if conversation_id is not None:
            kwargs["conversation_id"] = conversation_id
        result = handler(**kwargs)
        _finalize_task(task_id, bool(result.get("success", True)))
    except Exception as e:  # noqa: BLE001
        log.exception("handler %s failed", kind)
        _finalize_task(task_id, False, f"{type(e).__name__}: {e}")


def dispatch(max_concurrent: int = 5, batch_size: int = 10) -> dict:
    """Берёт до batch_size задач и запускает их параллельно (макс. max_concurrent)."""
    with SessionLocal() as db:
        rows = (
            db.query(models.AgentTask)
            .filter(models.AgentTask.status == "pending")
            .filter(models.AgentTask.scheduled_at <= datetime.utcnow())
            .order_by(
                models.AgentTask.priority.desc(),
                models.AgentTask.scheduled_at.asc(),
            )
            .limit(batch_size)
            .all()
        )
        task_ids = [r.id for r in rows]

    if not task_ids:
        return {"picked": 0}

    with ThreadPoolExecutor(max_workers=max_concurrent) as ex:
        list(ex.map(_process_one, task_ids))
    return {"picked": len(task_ids)}


# === Регистрация handlers (импорты вынесены сюда чтобы избежать циклов) ===

def _register_default_handlers() -> None:
    from worker.agents import outreach  # noqa: PLC0415

    def _h_first_touch(*, company_id: int, task_id: int | None = None, **_: object) -> dict:
        return outreach.run_first_touch(company_id=company_id, task_id=task_id)

    def _h_continue(
        *, conversation_id: int, company_id: int | None = None,
        task_id: int | None = None, **_: object,
    ) -> dict:
        return outreach.run_continue(
            conversation_id=conversation_id, company_id=company_id, task_id=task_id,
        )

    register(models.TASK_OUTREACH_FIRST, _h_first_touch)
    register(models.TASK_OUTREACH_CONT, _h_continue)


_register_default_handlers()
