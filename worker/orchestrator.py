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
    quota_left = 0
    with SessionLocal() as db:
        if kill_switch_active(db):
            return {"skipped": "kill_switch_active"}

        # 1) Companies без касаний → ставим outreach.first_touch
        # Логика:
        # - auto_outreach_enabled=False — полностью ручной режим, скипаем.
        # - иначе: считаем сколько first_touch уже enqueue'ено за СЕГОДНЯ;
        #   если < daily_outreach_quota — добираем разницу, выбирая prospects
        #   с самым высоким score (>= score_threshold).
        if _settings.auto_outreach_enabled:
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            # Quota = реально отправленные письма за сегодня. AgentTask.count
            # ловит черновики которые завалились до отправки (SMTP fail / Auditor
            # reject) и неправильно блокирует свежие касания. Считаем только
            # письма которые реально дошли до SMTP successfully (status=sent).
            today_count = (
                db.query(models.OutboxMessage)
                .filter(models.OutboxMessage.status == models.OUTBOX_SENT)
                .filter(models.OutboxMessage.created_at >= today_start)
                .count()
            )
            quota_left = max(0, _settings.daily_outreach_quota - today_count)
            if quota_left > 0:
                # Берём топ по score — реально горячих лидов сначала.
                # Исключаем компании на которые AI уже потратил токены
                # (есть OutboxMessage в любом статусе кроме 'rejected').
                # Auditor.reject = AI не успел реально подумать или сделал
                # шаблон → можно retry. Все остальные статусы (sent /
                # holding / failed / cancelled) = AI потратил токены на
                # анализ, повторять = жечь токены повторно.
                already_drafted_subq = (
                    select(models.OutboxMessage.company_id)
                    .where(models.OutboxMessage.status != models.OUTBOX_REJECTED)
                )
                in_flight_subq = (
                    select(models.AgentTask.company_id)
                    .where(models.AgentTask.kind == models.TASK_OUTREACH_FIRST)
                    .where(models.AgentTask.status.in_(("pending", "running")))
                )
                prospects = (
                    db.query(models.Company)
                    .filter(models.Company.stage == models.STAGE_PROSPECT)
                    .filter(models.Company.needs_human.is_(False))
                    .filter(
                        (models.Company.score.is_(None))
                        | (models.Company.score >= _settings.score_threshold)
                    )
                    .filter(not_(models.Company.id.in_(already_drafted_subq)))
                    .filter(not_(models.Company.id.in_(in_flight_subq)))
                    .order_by(models.Company.score.desc())
                    .limit(quota_left)
                    .all()
                )
                for c in prospects:
                    if enq_first >= quota_left:
                        break
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
