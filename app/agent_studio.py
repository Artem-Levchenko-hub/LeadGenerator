"""FastAPI router для дашборда агентной студии (Спринт 2).

Подключается в `app/main.py`:
    from app.agent_studio import router as agent_studio_router
    app.include_router(agent_studio_router)

Шаблоны лежат в `app/templates/studio/`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from app.auth import get_current_user, log_activity
from app.config import settings
from app.database import get_db
from app import models


router = APIRouter()

APP_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_ROOT / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Jinja-globals доступные в шаблонах студии
templates.env.globals["STAGE_LABELS"] = models.STAGE_LABELS
templates.env.globals["STAGE_ORDER"] = models.STAGE_ORDER


def _require_user(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return user


def _require_admin(request: Request):
    user = _require_user(request)
    if not user.is_admin:
        raise HTTPException(403, "admin only")
    return user


def _get_kill_switch(db: Session) -> models.KillSwitch:
    ks = db.query(models.KillSwitch).filter_by(id=1).one_or_none()
    if not ks:
        ks = models.KillSwitch(id=1, state="running")
        db.add(ks)
        db.commit()
    return ks


# ============================================================
# === /control — командный центр =============================
# ============================================================

@router.get("/control", response_class=HTMLResponse)
def control_page(request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request)
    ks = _get_kill_switch(db)

    # Лимиты на сегодня
    today = date.today().isoformat()
    quotas = db.query(models.DailyQuota).filter_by(date=today).all()
    quota_map = {q.channel: q for q in quotas}
    channels = [models.CHANNEL_EMAIL, models.CHANNEL_TELEGRAM, models.CHANNEL_SMS, models.CHANNEL_CALL]
    quotas_view = []
    limits_default = {
        models.CHANNEL_EMAIL:    settings.daily_email_limit,
        models.CHANNEL_TELEGRAM: settings.daily_telegram_limit,
        models.CHANNEL_SMS:      settings.daily_sms_limit,
        models.CHANNEL_CALL:     settings.daily_call_limit,
    }
    for ch in channels:
        q = quota_map.get(ch)
        sent = q.sent_count if q else 0
        limit = (q.limit_count if q and q.limit_count else limits_default.get(ch, 0))
        pct = round((sent / limit * 100) if limit else 0, 1)
        quotas_view.append({
            "channel": ch, "sent": sent, "limit": limit, "pct": pct,
        })

    # Последние 20 запусков агентов
    recent_runs = (
        db.query(models.AgentRun)
        .order_by(models.AgentRun.id.desc())
        .limit(20)
        .all()
    )

    # LLM-cost за сутки (агрегат по моделям)
    yesterday = datetime.utcnow() - timedelta(hours=24)
    cost_rows = (
        db.query(
            models.AgentRun.model,
            func.count(models.AgentRun.id).label("runs"),
            func.sum(models.AgentRun.cost_usd).label("cost"),
            func.sum(models.AgentRun.input_tokens).label("in_t"),
            func.sum(models.AgentRun.output_tokens).label("out_t"),
            func.sum(models.AgentRun.cache_read_tokens).label("cache_r"),
        )
        .filter(models.AgentRun.started_at >= yesterday)
        .group_by(models.AgentRun.model)
        .all()
    )
    cost_total = sum((r.cost or 0) for r in cost_rows)

    return templates.TemplateResponse(
        request, "studio/control.html",
        {
            "user": user,
            "kill_switch": ks,
            "quotas": quotas_view,
            "recent_runs": recent_runs,
            "cost_rows": cost_rows,
            "cost_total": round(cost_total, 4),
            "budget_usd": settings.daily_llm_budget_usd,
        },
    )


@router.post("/admin/kill-switch")
def admin_kill_switch(
    request: Request,
    state: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_admin(request)
    if state not in ("running", "paused_manual", "paused_budget", "paused_audit"):
        raise HTTPException(400, "invalid state")
    ks = _get_kill_switch(db)
    ks.state = state
    ks.reason = reason or None
    ks.set_by_user_id = user.id
    ks.set_at = datetime.utcnow()
    db.add(ks)
    log_activity(
        db, user.id, action="kill_switch_changed",
        meta={"state": state, "reason": reason},
    )
    db.commit()
    return RedirectResponse("/control", status_code=303)


# ============================================================
# === /companies — список со стадиями ========================
# ============================================================

@router.get("/companies", response_class=HTMLResponse)
def companies_page(
    request: Request,
    stage: str = "",
    needs_human: int = 0,
    db: Session = Depends(get_db),
):
    user = _require_user(request)
    q = db.query(models.Company)
    if stage:
        q = q.filter(models.Company.stage == stage)
    if needs_human:
        q = q.filter(models.Company.needs_human.is_(True))
    companies = q.order_by(models.Company.last_stage_change_at.desc()).limit(200).all()

    # Воронка — кол-во по стадиям
    funnel_rows = (
        db.query(models.Company.stage, func.count(models.Company.id))
        .group_by(models.Company.stage)
        .all()
    )
    funnel = {s: 0 for s in models.STAGE_ORDER}
    for s, c in funnel_rows:
        funnel[s] = c

    return templates.TemplateResponse(
        request, "studio/companies.html",
        {
            "user": user,
            "companies": companies,
            "filter_stage": stage,
            "filter_needs_human": needs_human,
            "funnel": funnel,
        },
    )


# ============================================================
# === /company/{id} — карточка компании ======================
# ============================================================

@router.get("/company/{company_id}", response_class=HTMLResponse)
def company_page(
    company_id: int, request: Request, db: Session = Depends(get_db),
):
    user = _require_user(request)
    company = db.query(models.Company).filter_by(id=company_id).one_or_none()
    if not company:
        raise HTTPException(404, "company not found")

    weaknesses = (
        db.query(models.LeadWeakness)
        .filter_by(company_id=company_id)
        .order_by(models.LeadWeakness.id.desc())
        .all()
    )
    convs = (
        db.query(models.Conversation)
        .filter_by(company_id=company_id)
        .order_by(models.Conversation.id.desc())
        .all()
    )
    history = (
        db.query(models.StageHistory)
        .filter_by(company_id=company_id)
        .order_by(models.StageHistory.id.desc())
        .all()
    )
    outbox = (
        db.query(models.OutboxMessage)
        .filter_by(company_id=company_id)
        .order_by(models.OutboxMessage.id.desc())
        .limit(50)
        .all()
    )
    runs = (
        db.query(models.AgentRun)
        .filter_by(company_id=company_id)
        .order_by(models.AgentRun.id.desc())
        .limit(20)
        .all()
    )

    # Сообщения по conversation
    convs_with_msgs = []
    for c in convs:
        msgs = (
            db.query(models.Message)
            .filter_by(conversation_id=c.id)
            .order_by(models.Message.id.asc())
            .all()
        )
        convs_with_msgs.append({"conv": c, "messages": msgs})

    return templates.TemplateResponse(
        request, "studio/company.html",
        {
            "user": user,
            "company": company,
            "weaknesses": weaknesses,
            "conversations": convs_with_msgs,
            "history": history,
            "outbox": outbox,
            "runs": runs,
        },
    )


# ============================================================
# === /outbox — что отправится в ближайшие минуты ============
# ============================================================

@router.get("/outbox", response_class=HTMLResponse)
def outbox_page(request: Request, db: Session = Depends(get_db)):
    user = _require_user(request)
    holding = (
        db.query(models.OutboxMessage)
        .filter(models.OutboxMessage.status.in_((
            models.OUTBOX_DRAFT, models.OUTBOX_HOLDING,
        )))
        .order_by(models.OutboxMessage.send_after.asc())
        .limit(50)
        .all()
    )
    sent_today = (
        db.query(models.OutboxMessage)
        .filter(models.OutboxMessage.status == models.OUTBOX_SENT)
        .filter(models.OutboxMessage.sent_at >= datetime.utcnow() - timedelta(hours=24))
        .order_by(models.OutboxMessage.sent_at.desc())
        .limit(50)
        .all()
    )
    rejected = (
        db.query(models.OutboxMessage)
        .filter(models.OutboxMessage.status == models.OUTBOX_REJECTED)
        .order_by(models.OutboxMessage.id.desc())
        .limit(20)
        .all()
    )
    return templates.TemplateResponse(
        request, "studio/outbox.html",
        {
            "user": user,
            "holding": holding,
            "sent_today": sent_today,
            "rejected": rejected,
            "now_utc": datetime.utcnow(),
        },
    )


@router.post("/company/{company_id}/trigger-outreach")
def trigger_outreach(
    company_id: int, request: Request, db: Session = Depends(get_db),
):
    """Ручной запуск Outreach Agent на конкретную компанию.

    Кладёт задачу в agent_tasks(kind=outreach.first_touch) с priority=10,
    воркер подхватит на ближайшем тике (≤60 сек) и запустит агента.
    """
    user = _require_user(request)
    company = db.query(models.Company).filter_by(id=company_id).one_or_none()
    if not company:
        raise HTTPException(404, "company not found")

    # Проверка не висит ли уже задача
    existing = (
        db.query(models.AgentTask)
        .filter_by(kind=models.TASK_OUTREACH_FIRST, company_id=company_id)
        .filter(models.AgentTask.status.in_(("pending", "running")))
        .first()
    )
    if existing:
        return RedirectResponse(
            f"/company/{company_id}?msg=already_pending",
            status_code=303,
        )

    task = models.AgentTask(
        kind=models.TASK_OUTREACH_FIRST,
        company_id=company_id,
        priority=10,
        scheduled_at=datetime.utcnow(),
        payload={"triggered_by_user_id": user.id},
    )
    db.add(task)
    log_activity(
        db, user.id, action="outreach_triggered_manual",
        meta={"company_id": company_id},
    )
    db.commit()
    return RedirectResponse(
        f"/company/{company_id}?msg=outreach_queued",
        status_code=303,
    )


@router.post("/api/outbox/{outbox_id}/cancel")
def api_outbox_cancel(
    outbox_id: int, request: Request, db: Session = Depends(get_db),
):
    user = _require_user(request)
    msg = db.query(models.OutboxMessage).filter_by(id=outbox_id).one_or_none()
    if not msg:
        raise HTTPException(404, "outbox message not found")
    if msg.status not in (models.OUTBOX_DRAFT, models.OUTBOX_HOLDING):
        raise HTTPException(400, f"cannot cancel: status={msg.status}")
    msg.status = models.OUTBOX_CANCELLED
    msg.audit_notes = (msg.audit_notes or "") + f" | cancelled by user_id={user.id}"
    db.add(msg)
    log_activity(db, user.id, action="outbox_cancelled", meta={"outbox_id": outbox_id})
    db.commit()
    return RedirectResponse("/outbox", status_code=303)


@router.get("/admin/outbox/recall/{token}")
def admin_outbox_recall(token: str, db: Session = Depends(get_db)):
    """Открытая ссылка для отзыва outbox-сообщения по recall_token —
    без авторизации, чтобы можно было быстро отозвать с телефона."""
    msg = (
        db.query(models.OutboxMessage)
        .filter_by(recall_token=token)
        .one_or_none()
    )
    if not msg:
        return PlainTextResponse("not found", status_code=404)
    if msg.status not in (models.OUTBOX_DRAFT, models.OUTBOX_HOLDING):
        return PlainTextResponse(
            f"cannot recall: status={msg.status}", status_code=400,
        )
    msg.status = models.OUTBOX_CANCELLED
    msg.audit_notes = (msg.audit_notes or "") + " | cancelled via recall_token"
    db.add(msg)
    db.commit()
    return PlainTextResponse(f"OK cancelled outbox#{msg.id}")


# ============================================================
# === /api/optout — отписка по токену ========================
# ============================================================

@router.get("/api/optout")
def api_optout(token: str = "", db: Session = Depends(get_db)):
    """Открытая ссылка из List-Unsubscribe header: добавляет адрес в blacklist."""
    if not token:
        return PlainTextResponse("token required", status_code=400)
    msg = (
        db.query(models.OutboxMessage)
        .filter_by(recall_token=token)
        .one_or_none()
    )
    if not msg or not msg.to_address:
        return PlainTextResponse("invalid token", status_code=404)
    addr = msg.to_address.lower()
    existing = db.query(models.Blacklist).filter_by(
        kind="email", value=addr,
    ).first()
    if not existing:
        db.add(models.Blacklist(
            kind="email", value=addr, reason=f"opt-out via outbox#{msg.id}",
        ))
        db.commit()
    return PlainTextResponse(
        f"Вы успешно отписаны. Адрес {addr} удалён из нашей базы.\n"
        "You have been unsubscribed."
    )


# ============================================================
# === /feed — живая лента работы агентов (HTMX polling) ======
# ============================================================

@router.get("/feed", response_class=HTMLResponse)
def feed_page(request: Request, db: Session = Depends(get_db)):
    user = _require_user(request)
    return templates.TemplateResponse(
        request, "studio/feed.html",
        {"user": user},
    )


# ============================================================
# === /system-history — эволюция работы агентов ==============
# ============================================================

@router.get("/system-history", response_class=HTMLResponse)
def system_history_page(
    request: Request,
    days: int = 7,
    agent: str = "",
    db: Session = Depends(get_db),
):
    """История развития системы:
    - Сводка по дням: запуски агентов, стоимость, успех/ошибки
    - Распределение по агентам
    - Top-cost запуски
    - Все strategy_proposals
    """
    user = _require_user(request)

    since = datetime.utcnow() - timedelta(days=days)

    # === Daily aggregate ===
    runs_q = db.query(models.AgentRun).filter(models.AgentRun.started_at >= since)
    if agent:
        runs_q = runs_q.filter(models.AgentRun.agent_kind == agent)

    daily_rows: dict[str, dict] = {}
    for r in runs_q.all():
        day = r.started_at.date().isoformat()
        d = daily_rows.setdefault(day, {
            "day": day,
            "runs": 0, "success": 0, "failed": 0,
            "cost": 0.0, "tokens": 0,
            "by_agent": {},
        })
        d["runs"] += 1
        if r.success: d["success"] += 1
        else: d["failed"] += 1
        d["cost"] += float(r.cost_usd or 0)
        d["tokens"] += int(r.input_tokens or 0) + int(r.output_tokens or 0)
        d["by_agent"][r.agent_kind] = d["by_agent"].get(r.agent_kind, 0) + 1

    daily = sorted(daily_rows.values(), key=lambda d: d["day"], reverse=True)

    # === Top-cost runs ===
    top_cost = (
        db.query(models.AgentRun)
        .filter(models.AgentRun.started_at >= since)
        .order_by(models.AgentRun.cost_usd.desc())
        .limit(10)
        .all()
    )

    # === Stage history aggregate ===
    stages_q = (
        db.query(models.StageHistory.to_stage, func.count(models.StageHistory.id))
        .filter(models.StageHistory.changed_at >= since)
        .group_by(models.StageHistory.to_stage)
        .all()
    )
    stages_by_kind = {s: c for s, c in stages_q}

    # === Strategy proposals ===
    strat = (
        db.query(models.StrategyProposal)
        .order_by(models.StrategyProposal.id.desc())
        .limit(20)
        .all()
    )

    # === Errors (failed runs in window) ===
    errors_count = (
        db.query(func.count(models.AgentRun.id))
        .filter(models.AgentRun.started_at >= since)
        .filter(models.AgentRun.success.is_(False))
        .scalar() or 0
    )
    total_count = (
        db.query(func.count(models.AgentRun.id))
        .filter(models.AgentRun.started_at >= since)
        .scalar() or 0
    )
    total_cost = (
        db.query(func.sum(models.AgentRun.cost_usd))
        .filter(models.AgentRun.started_at >= since)
        .scalar() or 0
    )

    # Список доступных agent_kind для фильтра
    agent_kinds = [
        k for k, in db.query(models.AgentRun.agent_kind).distinct().all() if k
    ]

    return templates.TemplateResponse(
        request, "studio/system_history.html",
        {
            "user": user,
            "days": days,
            "filter_agent": agent,
            "daily": daily,
            "top_cost": top_cost,
            "stages_by_kind": stages_by_kind,
            "strategy_proposals": strat,
            "errors_count": errors_count,
            "total_count": total_count,
            "total_cost": round(float(total_cost), 4),
            "agent_kinds": agent_kinds,
        },
    )


@router.get("/feed/items", response_class=HTMLResponse)
def feed_items(request: Request, db: Session = Depends(get_db)):
    """HTMX-фрагмент: последние 30 событий (agent_runs + stage_history + outbox)."""
    _require_user(request)
    runs = (
        db.query(models.AgentRun)
        .order_by(models.AgentRun.id.desc())
        .limit(20)
        .all()
    )
    history = (
        db.query(models.StageHistory)
        .order_by(models.StageHistory.id.desc())
        .limit(20)
        .all()
    )
    outbox = (
        db.query(models.OutboxMessage)
        .order_by(models.OutboxMessage.id.desc())
        .limit(20)
        .all()
    )

    items = []
    for r in runs:
        items.append({
            "ts": r.started_at, "kind": "agent",
            "icon": "🤖", "agent": r.agent_kind,
            "text": (
                f"{r.agent_kind} {'✓' if r.success else '✗'} "
                f"({r.iterations}it · ${round(r.cost_usd or 0, 4)}) "
                f"{(r.summary or '')[:120]}"
            ),
            "company_id": r.company_id,
        })
    for h in history:
        items.append({
            "ts": h.changed_at, "kind": "stage",
            "icon": "→", "agent": h.changed_by_agent,
            "text": (
                f"{models.STAGE_LABELS.get(h.from_stage, h.from_stage or '?')} → "
                f"{models.STAGE_LABELS.get(h.to_stage, h.to_stage)}"
                + (f" — {h.reason}" if h.reason else "")
            ),
            "company_id": h.company_id,
        })
    for m in outbox:
        items.append({
            "ts": m.created_at, "kind": "outbox",
            "icon": "✉",
            "agent": m.created_by_agent or "—",
            "text": (
                f"[{m.channel}/{m.status}] {m.to_address} "
                f"\"{(m.subject or m.body_text)[:80]}\""
            ),
            "company_id": m.company_id,
        })
    items.sort(key=lambda x: x["ts"] or datetime.min, reverse=True)
    items = items[:30]

    return templates.TemplateResponse(
        request, "studio/_feed_items.html",
        {"items": items},
    )
