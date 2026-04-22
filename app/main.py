import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import check_user
from .config import settings
from .database import get_db, init_db
from .models import Lead, RunLog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = None
    try:
        from pipeline.scheduler import start_scheduler
        scheduler = start_scheduler()
    except Exception:
        logger.exception("Scheduler did not start")
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Stenvik Lead Pipeline", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

PRIORITY_LABELS = {
    5: ("Горячий", "danger"),
    4: ("Тёплый", "warning"),
    3: ("Средний", "info"),
    2: ("Слабый", "secondary"),
    1: ("Не наш", "secondary"),
    0: ("Новый", "light"),
}

STATUS_LABELS = {
    "new": "Новый",
    "in_progress": "В работе",
    "contacted": "Связались",
    "qualified": "Квалифицирован",
    "won": "Сделка",
    "lost": "Отказ",
}

templates.env.globals["priority_labels"] = PRIORITY_LABELS
templates.env.globals["status_labels"] = STATUS_LABELS


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: str = Depends(check_user),
    db: Session = Depends(get_db),
):
    total = db.scalar(select(func.count(Lead.id))) or 0
    by_priority = dict(
        db.execute(select(Lead.ai_priority, func.count(Lead.id)).group_by(Lead.ai_priority)).all()
    )
    by_status = dict(
        db.execute(select(Lead.status, func.count(Lead.id)).group_by(Lead.status)).all()
    )
    recent_runs = db.scalars(
        select(RunLog).order_by(RunLog.started_at.desc()).limit(5)
    ).all()
    top_leads = db.scalars(
        select(Lead)
        .where(Lead.status == "new")
        .order_by(Lead.ai_priority.desc(), Lead.created_at.desc())
        .limit(10)
    ).all()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "total": total,
            "by_priority": by_priority,
            "by_status": by_status,
            "recent_runs": recent_runs,
            "top_leads": top_leads,
        },
    )


@app.get("/leads", response_class=HTMLResponse)
def leads_list(
    request: Request,
    user: str = Depends(check_user),
    db: Session = Depends(get_db),
    priority: int | None = None,
    status_filter: str | None = None,
    city: str | None = None,
    q: str | None = None,
    page: int = 1,
):
    per_page = 50
    stmt = select(Lead)
    if priority is not None:
        stmt = stmt.where(Lead.ai_priority == priority)
    if status_filter:
        stmt = stmt.where(Lead.status == status_filter)
    if city:
        stmt = stmt.where(Lead.city == city)
    if q:
        stmt = stmt.where(Lead.company_name.ilike(f"%{q}%"))
    stmt = stmt.order_by(Lead.ai_priority.desc(), Lead.created_at.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    leads = db.scalars(stmt).all()

    cities = db.scalars(
        select(Lead.city).where(Lead.city.is_not(None)).distinct().order_by(Lead.city)
    ).all()

    return templates.TemplateResponse(
        request,
        "leads_list.html",
        {
            "user": user,
            "leads": leads,
            "cities": cities,
            "priority": priority,
            "status_filter": status_filter,
            "city": city,
            "q": q,
            "page": page,
        },
    )


@app.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail(
    lead_id: int,
    request: Request,
    user: str = Depends(check_user),
    db: Session = Depends(get_db),
):
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Лид не найден")
    return templates.TemplateResponse(
        request, "lead_detail.html", {"user": user, "lead": lead}
    )


@app.post("/leads/{lead_id}/update")
def lead_update(
    lead_id: int,
    user: str = Depends(check_user),
    db: Session = Depends(get_db),
    status_value: str = Form(""),
    assigned_to: str = Form(""),
    notes: str = Form(""),
):
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404)
    if status_value and status_value in STATUS_LABELS:
        lead.status = status_value
    if assigned_to is not None:
        lead.assigned_to = assigned_to or None
    if notes is not None:
        lead.notes = notes or None
    db.commit()
    return RedirectResponse(url=f"/leads/{lead_id}", status_code=303)


@app.post("/pipeline/run")
def pipeline_run_manual(
    user: str = Depends(check_user),
    limit: int = 5,
):
    from pipeline.runner import run_pipeline_once
    result = run_pipeline_once(limit=limit)
    return {"ok": True, **result}


@app.get("/health")
def health():
    return {"ok": True}
