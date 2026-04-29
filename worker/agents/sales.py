"""Sales Manager Agent — Sprint 4.

Триггер: Conversation.state in (engaged, qualifying), есть свежий inbound.

Задача: BANT-квалификация (Budget/Authority/Need/Timeline). 1 вопрос за касание,
не допрос. Когда все 4 пункта собраны → state='ready_for_proposal' и эскалация
человеку для подготовки КП. Если сливается — state='lost'.

Переиспользует tools Outreach Agent + добавляет 2 sales-специфичных:
- record_qualification — запись BANT-данных по мере выяснения
- mark_objection — фиксирует возражение и применённый ответ из playbook
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.config import settings
from app.database import SessionLocal
from app import models
from ai import prompts
from worker.llm import run_react_loop
from worker.agents.tools.definitions import OUTREACH_TOOLS
from worker.agents.tools.handlers import OUTREACH_HANDLERS


log = logging.getLogger(__name__)


# === Sales-специфичные tools ===

RECORD_QUALIFICATION = {
    "name": "record_qualification",
    "description": (
        "Записать BANT-квалификацию: что узнал из переписки. Вызывай по мере "
        "появления данных, не сразу. Каждый вызов добавляет новую запись — "
        "можно несколько раз по ходу разговора."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "company_id": {"type": "integer"},
            "budget_band": {
                "type": "string",
                "description": "<75k | 75-200k | 200-500k | 500k+ | unknown",
            },
            "has_decision_maker": {
                "type": "boolean",
                "description": "Общается ли с тобой ЛПР? true/false/null если непонятно",
            },
            "timeline": {
                "type": "string",
                "description": "asap | 1-3m | 3-6m | someday | unknown",
            },
            "urgency": {
                "type": "string",
                "description": "high | med | low",
            },
            "notes": {
                "type": "string",
                "description": "Что узнал в этой итерации, цитаты из его сообщений",
            },
        },
        "required": ["company_id"],
    },
}

MARK_OBJECTION = {
    "name": "mark_objection",
    "description": (
        "Записать возражение клиента и какой ответ ты применил. Используй "
        "playbook возражений из системного промпта. Это даёт нам базу для "
        "обучения и улучшения playbook'а."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "company_id": {"type": "integer"},
            "kind": {
                "type": "string",
                "description": "price | trust | timing | scope | competitor | uncertain | other",
            },
            "text": {"type": "string", "description": "Что клиент сказал (цитата)"},
            "response_used": {"type": "string", "description": "Какой ответ ты дал"},
        },
        "required": ["company_id", "kind", "text"],
    },
}


def _record_qualification_handler(
    *, company_id: int, budget_band: str | None = None,
    has_decision_maker: bool | None = None, timeline: str | None = None,
    urgency: str | None = None, notes: str | None = None,
) -> str:
    with SessionLocal() as db:
        q = models.Qualification(
            company_id=company_id,
            budget_band=(budget_band or "")[:32] or None,
            has_decision_maker=has_decision_maker,
            timeline=(timeline or "")[:64] or None,
            urgency=(urgency or "")[:16] or None,
            notes=(notes or "")[:2000] or None,
        )
        db.add(q)
        db.commit()
        return f"OK qualification_id={q.id}"


def _mark_objection_handler(
    *, company_id: int, kind: str, text: str, response_used: str = "",
) -> str:
    with SessionLocal() as db:
        o = models.ObjectionsLog(
            company_id=company_id,
            kind=(kind or "other")[:64],
            text=(text or "")[:2000],
            response_used=(response_used or "")[:2000] or None,
        )
        db.add(o)
        db.commit()
        return f"OK objection_id={o.id}"


SALES_TOOLS = OUTREACH_TOOLS + [RECORD_QUALIFICATION, MARK_OBJECTION]
SALES_HANDLERS = {
    **OUTREACH_HANDLERS,
    "record_qualification": _record_qualification_handler,
    "mark_objection": _mark_objection_handler,
}


# === Запуск ===

def _system_blocks() -> list[dict]:
    return [
        {"type": "text", "text": prompts.build_common_prefix()},
        {"type": "text", "text": prompts.SALES_MANAGER_SYSTEM},
    ]


def _read_conv_facts(conversation_id: int) -> dict | None:
    with SessionLocal() as db:
        conv = db.query(models.Conversation).filter_by(id=conversation_id).one_or_none()
        if not conv:
            return None
        company = None
        if conv.company_id:
            company = db.query(models.Company).filter_by(id=conv.company_id).one_or_none()
        # Сколько BANT уже собрано
        prev_quals = (
            db.query(models.Qualification)
            .filter_by(company_id=conv.company_id)
            .order_by(models.Qualification.id.desc())
            .limit(5)
            .all()
        )
        bant_summary = []
        for q in prev_quals:
            bant_summary.append({
                "ts": q.captured_at.isoformat() + "Z" if q.captured_at else None,
                "budget_band": q.budget_band,
                "has_decision_maker": q.has_decision_maker,
                "timeline": q.timeline,
                "urgency": q.urgency,
                "notes": (q.notes or "")[:200],
            })
        return {
            "conversation_id": conv.id,
            "company_id": conv.company_id,
            "company_name": company.name if company else None,
            "company_industry": company.industry if company else None,
            "company_city": company.city if company else None,
            "company_score": company.score if company else None,
            "state": conv.state,
            "channel": conv.channel,
            "topic": conv.topic,
            "bot_messages_count": conv.bot_messages_count or 0,
            "bant_so_far": bant_summary,
        }


def run_sales_qualification(
    conversation_id: int,
    company_id: int | None = None,
    task_id: int | None = None,
) -> dict:
    facts = _read_conv_facts(conversation_id)
    if not facts:
        return {"success": False, "error": f"conversation {conversation_id} not found"}

    user_msg = f"""Задача: sales.continue для conversation #{conversation_id}.

⚠️ Используй ТОЛЬКО факты из БД ниже + результаты read_thread.

```json
{json.dumps(facts, ensure_ascii=False, indent=2, default=str)}
```

ПРАВИЛА:
1. Сначала `read_thread({conversation_id})` — прочитай ВСЮ историю разговора.
2. Применяй BANT, по 1 вопросу за касание (не допрашивай). Записывай данные
   через `record_qualification` по мере выяснения.
3. Если клиент возражает — используй playbook из системного промпта,
   фиксируй через `mark_objection`.
4. Если все 4 BANT-пункта собраны (видно из bant_so_far + новые ответы) →
   `update_conversation_state(state='ready_for_proposal', reason='BANT complete')`
   + `escalate_to_human(reason='ready_for_proposal — готовь КП')`.
5. Если лид сливается / 5+ ботских реплик подряд без человеческого
   ответа → `update_conversation_state(state='lost')` или `'stalled'` + `finish`.
6. Если запрашивает встречу/созвон → `escalate_to_human(reason='meeting_requested')`.
7. ВСЕГДА завершайся через `finish`.

Ответ должен быть в том же канале и в продолжение треда (передавай
`conversation_id={conversation_id}` в `draft_message`)."""

    record = run_react_loop(
        agent_kind="sales",
        system_blocks=_system_blocks(),
        user_message=user_msg,
        tools=SALES_TOOLS,
        tool_handlers=SALES_HANDLERS,
        model=settings.model_default,
        max_iterations=settings.outreach_max_iterations,
        task_id=task_id,
        company_id=company_id or facts["company_id"],
    )
    return {
        "success": record.success,
        "summary": record.summary,
        "iterations": record.iterations,
        "cost_usd": record.cost_usd,
        "error": record.error_text,
    }
