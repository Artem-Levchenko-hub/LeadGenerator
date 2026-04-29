"""Tactical Orchestrator — детерминированный (БЕЗ LLM) Python-планировщик.

Каждую минуту:
1. Читает kill_switch (если paused — выходит).
2. Находит Companies в stage=prospect без открытой задачи outreach.first_touch
   и ставит её.
3. Находит новые входящие messages.direction=in без задачи outreach.continue
   и ставит её.
4. (Спринт 4+) переходы между stage'ами и постановка задач Sales/Discovery/...
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import select, and_, not_, exists
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal


def kill_switch_active(db: Session) -> bool:
    ks = db.query(models.KillSwitch).filter_by(id=1).one_or_none()
    return bool(ks and ks.state != "running")


def has_open_task(db: Session, kind: str, company_id: int) -> bool:
    return db.query(
        exists().where(
            and_(
                models.AgentTask.kind == kind,
                models.AgentTask.company_id == company_id,
                models.AgentTask.status.in_(("pending", "running")),
            )
        )
    ).scalar()


def enqueue(
    db: Session,
    *,
    kind: str,
    company_id: int | None = None,
    conversation_id: int | None = None,
    payload: dict | None = None,
    priority: int = 5,
) -> models.AgentTask:
    task = models.AgentTask(
        kind=kind,
        company_id=company_id,
        conversation_id=conversation_id,
        payload=payload or {},
        priority=priority,
        scheduled_at=datetime.utcnow(),
    )
    db.add(task)
    db.commit()
    return task


def tick() -> dict:
    """Один тик оркестратора. Возвращает отчёт о созданных задачах."""
    from app.config import settings as _settings

    enq_first = 0
    enq_cont = 0
    auto_off = not _settings.auto_outreach_enabled
    with SessionLocal() as db:
        if kill_switch_active(db):
            return {"skipped": "kill_switch_active"}

        # 1) Companies без касаний → ставим outreach.first_touch
        # ВАЖНО: только если AUTO_OUTREACH_ENABLED=true. Иначе лиды лежат в
        # БД, ждут что пользователь зайдёт на /company/{id} и нажмёт
        # «Запустить Outreach Agent» вручную. Это сохраняет LLM-токены —
        # AI не дёргается на каждого нового prospect'а автоматически.
        if not auto_off:
            prospects = (
                db.query(models.Company)
                .filter(models.Company.stage == models.STAGE_PROSPECT)
                .filter(models.Company.needs_human.is_(False))
                .order_by(models.Company.created_at.asc())
                .limit(20)
                .all()
            )
            for c in prospects:
                if has_open_task(db, models.TASK_OUTREACH_FIRST, c.id):
                    continue
                enqueue(db, kind=models.TASK_OUTREACH_FIRST, company_id=c.id)
                enq_first += 1

        # 2) Новые входящие → outreach.continue
        # Идея: для каждого conversation, у которого last_inbound_at > last_outbound_at
        # и нет открытой задачи outreach.continue → ставим её.
        convs = (
            db.query(models.Conversation)
            .filter(models.Conversation.state.in_((
                models.CONV_NEW, models.CONV_ENGAGED, models.CONV_QUALIFYING,
            )))
            .filter(models.Conversation.last_inbound_at.isnot(None))
            .all()
        )
        for conv in convs:
            if (
                conv.last_outbound_at and conv.last_inbound_at
                and conv.last_inbound_at <= conv.last_outbound_at
            ):
                continue
            if not conv.last_inbound_at:
                continue
            # Проверка наличия открытой continue-задачи для этого conv
            already = db.query(exists().where(and_(
                models.AgentTask.kind == models.TASK_OUTREACH_CONT,
                models.AgentTask.conversation_id == conv.id,
                models.AgentTask.status.in_(("pending", "running")),
            ))).scalar()
            if already:
                continue
            enqueue(
                db,
                kind=models.TASK_OUTREACH_CONT,
                company_id=conv.company_id,
                conversation_id=conv.id,
            )
            enq_cont += 1

    return {
        "enqueued_first_touch": enq_first,
        "enqueued_continue": enq_cont,
    }
