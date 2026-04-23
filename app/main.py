"""FastAPI веб-приложение для Stenvik Leads.

Запуск в dev:
    .venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8001

Запуск в prod:
    gunicorn app.main:app -w 2 -k uvicorn.workers.UvicornWorker -b 127.0.0.1:8001
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth import (
    get_current_user,
    hash_password,
    is_bootstrap_mode,
    log_activity,
    login_user,
    logout_user,
    validate_password,
    validate_username,
    verify_password,
)
from app.config import settings
from app.database import SessionLocal, get_db, init_db
from app.models import (
    DEAL_STATUS_LABELS,
    DEAL_STATUS_ORDER,
    ROLE_ADMIN,
    ROLE_SALES,
    ActivityLog,
    ProcessedLead,
    User,
)

logger = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_ROOT / "templates"
STATIC_DIR = APP_ROOT / "static"

# ==== App setup ====

app = FastAPI(title="Stenvik Leads", docs_url=None, redoc_url=None)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret,
    session_cookie="stenvik_session",
    max_age=60 * 60 * 24 * 14,  # 14 дней
    same_site="lax",
    https_only=False,  # Prod: поставим через env
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Jinja helpers
templates.env.globals["DEAL_STATUS_LABELS"] = DEAL_STATUS_LABELS
templates.env.globals["DEAL_STATUS_ORDER"] = DEAL_STATUS_ORDER
templates.env.globals["ROLE_ADMIN"] = ROLE_ADMIN
templates.env.globals["ROLE_SALES"] = ROLE_SALES


@app.on_event("startup")
def _startup():
    init_db()
    logger.info("DB initialized at %s", settings.database_url)


# ==== Хелперы ответов ====

def _render(request: Request, template: str, **ctx) -> HTMLResponse:
    ctx.setdefault("user", get_current_user(request))
    # Starlette >=0.31 новый сигнатура: (request, name, context)
    return templates.TemplateResponse(request, template, ctx)


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


# ==== Роуты: корень и auth ====

@app.get("/health")
def health():
    return {"ok": True, "service": "stenvik-leads"}


# PWA: Service Worker должен быть на корне чтобы иметь scope "/"
from fastapi.responses import FileResponse


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Отдаём SW с корня — чтобы scope был весь сайт."""
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache, no-store, max-age=0"},
    )


@app.get("/manifest.json", include_in_schema=False)
def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.json",
        media_type="application/manifest+json",
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC_DIR / "icons" / "favicon.png")


@app.get("/", response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db)):
    """Умный корень: куда вести зависит от состояния."""
    if is_bootstrap_mode(db):
        return _redirect("/register")
    user = get_current_user(request, db)
    if user is None:
        return _redirect("/login")
    return _redirect("/dashboard")


# ---- Registration (только bootstrap) ----

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    if not is_bootstrap_mode(db):
        # Обычный юзер не может регистрироваться сам — только через админку
        user = get_current_user(request, db)
        if user and user.is_admin:
            return _redirect("/admin/users")
        return _render(request, "register_closed.html")
    return _render(request, "register.html", bootstrap=True)


@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
):
    if not is_bootstrap_mode(db):
        raise HTTPException(status_code=403, detail="Регистрация закрыта")

    err = validate_username(username) or validate_password(password)
    if err is None and password != password2:
        err = "Пароли не совпадают"
    if err:
        return _render(request, "register.html", bootstrap=True, error=err, username=username.strip())

    user = User(
        username=username.strip(),
        password_hash=hash_password(password),
        role=ROLE_ADMIN,  # Первый юзер = админ
        is_active=True,
        created_at=datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_activity(db, user_id=user.id, action="register", meta={"bootstrap": True})
    login_user(request, user)
    return _redirect("/dashboard")


# ---- Login / Logout ----

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if is_bootstrap_mode(db):
        return _redirect("/register")
    if get_current_user(request, db):
        return _redirect("/dashboard")
    return _render(request, "login.html")


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    username = username.strip()
    user = db.scalar(select(User).where(User.username == username))

    if not user or not user.is_active or not verify_password(password, user.password_hash):
        log_activity(db, user_id=user.id if user else None, action="login_failed",
                     meta={"username": username})
        return _render(request, "login.html", error="Неверный логин или пароль", username=username)

    user.last_login_at = datetime.utcnow()
    db.commit()
    log_activity(db, user_id=user.id, action="login")
    login_user(request, user)
    return _redirect("/dashboard")


@app.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        log_activity(db, user_id=user.id, action="logout")
    logout_user(request)
    return _redirect("/login")


# ---- Dashboard (заглушка Phase 2 — Phase 3 допилит) ----

def _age_text(when: datetime | None) -> str:
    """Человекочитаемое 'сколько назад'."""
    if not when:
        return ""
    delta = datetime.utcnow() - when
    secs = int(delta.total_seconds())
    if secs < 60:
        return "только что"
    if secs < 3600:
        return f"{secs // 60} мин назад"
    if secs < 86400:
        return f"{secs // 3600} ч назад"
    if secs < 86400 * 7:
        return f"{secs // 86400} дн назад"
    return when.strftime("%d.%m")


def _apply_filter(q, filter_name: str | None, user: User, industry: str | None = None):
    """Применяет фильтр + опциональную индустрию к query по лидам."""
    if filter_name == "hot":
        q = q.where(ProcessedLead.priority >= 4, ProcessedLead.called == False)
    elif filter_name == "to_call":
        q = q.where(ProcessedLead.called == False,
                    ProcessedLead.deal_status.in_(("new", "in_work")))
    elif filter_name == "called":
        q = q.where(ProcessedLead.called == True)
    elif filter_name == "deal":
        q = q.where(ProcessedLead.deal_status == "deal")
    elif filter_name == "mine":
        q = q.where(ProcessedLead.assigned_to_id == user.id)
    elif filter_name == "ru":
        q = q.where(ProcessedLead.country == "RU")
    elif filter_name == "foreign":
        q = q.where(ProcessedLead.country != "RU")
    if industry:
        q = q.where(ProcessedLead.industry == industry)
    return q


def _collect_dashboard_ctx(
    db: Session, user: User, filter_name: str | None, industry: str | None = None,
) -> dict:
    """Единая точка сбора данных для /dashboard и partial endpoints."""
    # Счётчики для filter-bar (всегда по полной базе)
    total_all = db.scalar(select(func.count(ProcessedLead.id))) or 0
    hot_count = db.scalar(
        select(func.count(ProcessedLead.id))
        .where(ProcessedLead.priority >= 4, ProcessedLead.called == False)
    ) or 0
    to_call_count = db.scalar(
        select(func.count(ProcessedLead.id))
        .where(ProcessedLead.called == False,
               ProcessedLead.deal_status.in_(("new", "in_work")))
    ) or 0
    called_count_all = db.scalar(
        select(func.count(ProcessedLead.id))
        .where(ProcessedLead.called == True)
    ) or 0
    deal_count = db.scalar(
        select(func.count(ProcessedLead.id))
        .where(ProcessedLead.deal_status == "deal")
    ) or 0
    mine_count = db.scalar(
        select(func.count(ProcessedLead.id))
        .where(ProcessedLead.assigned_to_id == user.id)
    ) or 0
    ru_count = db.scalar(
        select(func.count(ProcessedLead.id))
        .where(ProcessedLead.country == "RU")
    ) or 0
    foreign_count = db.scalar(
        select(func.count(ProcessedLead.id))
        .where(ProcessedLead.country != "RU")
    ) or 0

    # Top индустрий (по количеству лидов во всей базе, не только в текущем фильтре —
    # чтобы чипы не «мигали» при смене filter'а)
    industry_rows = db.execute(
        select(ProcessedLead.industry, func.count(ProcessedLead.id))
        .where(ProcessedLead.industry.isnot(None), ProcessedLead.industry != "")
        .group_by(ProcessedLead.industry)
        .order_by(func.count(ProcessedLead.id).desc())
        .limit(16)
    ).all()
    top_industries = [(name, cnt) for (name, cnt) in industry_rows if name]

    # Применяем фильтр к выборке лидов.
    # Когда индустрия не выбрана — вторичная сортировка по индустрии, чтобы карточки
    # одной ниши кластеризовались рядом (проще обзванивать подряд).
    order_cols = [ProcessedLead.priority.desc(), ProcessedLead.analyzed_at.desc()]
    if not industry:
        order_cols = [ProcessedLead.priority.desc(),
                      ProcessedLead.industry.asc().nullslast(),
                      ProcessedLead.analyzed_at.desc()]
    q = select(ProcessedLead).order_by(*order_cols)
    q = _apply_filter(q, filter_name, user, industry)
    leads = list(db.scalars(q).all())

    # Обогащаем карточки «возрастом»
    now = datetime.utcnow()
    for l in leads:
        age = (now - l.analyzed_at).total_seconds() if l.analyzed_at else 0
        l.age_hours = age / 3600
        l.age_text = _age_text(l.analyzed_at)

    # Для summary считаем по ТЕКУЩЕЙ выборке (после фильтра), чтобы пользователь
    # видел распределение в рамках того, на что смотрит.
    counts_by_priority = {p: 0 for p in (5, 4, 3, 2, 1)}
    for l in leads:
        if l.priority in counts_by_priority:
            counts_by_priority[l.priority] += 1
    called_count_view = sum(1 for l in leads if l.called)

    return {
        "leads": leads,
        "total": len(leads),
        "counts_by_priority": counts_by_priority,
        "called_count": called_count_view,
        # Общие для filter-bar
        "total_all": total_all,
        "hot_count": hot_count,
        "to_call_count": to_call_count,
        "called_count_all": called_count_all,
        "deal_count": deal_count,
        "mine_count": mine_count,
        "ru_count": ru_count,
        "foreign_count": foreign_count,
        "filter": filter_name,
        # Индустрии
        "top_industries": top_industries,
        "current_industry": industry,
    }


def _dashboard_qs(filter: str | None, industry: str | None) -> str:
    """Собирает querystring для /dashboard-ендпоинтов (сохраняет filter+industry)."""
    from urllib.parse import urlencode
    params = {}
    if filter:
        params["filter"] = filter
    if industry:
        params["industry"] = industry
    return "?" + urlencode(params) if params else ""


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    filter: str | None = None,
    industry: str | None = None,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return _redirect("/login")

    ctx = _collect_dashboard_ctx(db, user, filter, industry)
    # В шаблоне filter-bar использует counts из ctx
    ctx["total"] = ctx["total_all"] if not (filter or industry) else ctx["total"]
    return _render(request, "dashboard.html", **ctx)


@app.get("/partials/leads-grid", response_class=HTMLResponse)
def partial_leads_grid(
    request: Request,
    filter: str | None = None,
    industry: str | None = None,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401)
    ctx = _collect_dashboard_ctx(db, user, filter, industry)
    return templates.TemplateResponse(request, "partials/leads_grid.html", ctx)


@app.get("/partials/summary", response_class=HTMLResponse)
def partial_summary(
    request: Request,
    filter: str | None = None,
    industry: str | None = None,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401)
    ctx = _collect_dashboard_ctx(db, user, filter, industry)
    qs = _dashboard_qs(filter, industry)
    # Возвращаем с оберткой <div id="summary">
    html = '<div class="summary-bar" id="summary" hx-get="/partials/summary' + qs + \
        '" hx-trigger="every 30s" hx-swap="outerHTML">' + \
        templates.get_template("partials/summary_bar.html").render(**ctx) + \
        '</div>'
    return HTMLResponse(html)


# ==== Роуты: детальная страница лида ====

def _load_lead(db: Session, lead_id: int) -> ProcessedLead:
    lead = db.get(ProcessedLead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Лид не найден")
    return lead


@app.get("/lead/{lead_id}", response_class=HTMLResponse)
def lead_detail(lead_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return _redirect("/login")
    lead = _load_lead(db, lead_id)
    return _render(request, "lead_detail.html", lead=lead)


# ==== HTMX-эндпоинты для CRM-полей ====

def _ensure_sales_access(request: Request, db: Session) -> User:
    """И продажники, и админы могут менять CRM-поля."""
    return require_user_dep(request, db)


def require_user_dep(request: Request, db: Session) -> User:
    """Локальный хелпер чтобы не тянуть Depends в каждый endpoint."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return user


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


@app.patch("/api/leads/{lead_id}/called", response_class=HTMLResponse)
def toggle_called(
    lead_id: int, request: Request, db: Session = Depends(get_db),
):
    user = _ensure_sales_access(request, db)
    lead = _load_lead(db, lead_id)

    was = bool(lead.called)
    lead.called = not was
    lead.called_at = datetime.utcnow() if lead.called else None
    lead.called_by_id = user.id if lead.called else None
    lead.updated_at = datetime.utcnow()

    log_activity(
        db, user_id=user.id, action="lead_called" if lead.called else "lead_uncalled",
        lead_id=lead.id,
    )
    db.commit()
    db.refresh(lead)

    if _is_htmx(request):
        return templates.TemplateResponse(
            request, "partials/call_toggle.html", {"lead": lead},
        )
    return JSONResponse({"ok": True, "called": lead.called})


@app.patch("/api/leads/{lead_id}/status", response_class=HTMLResponse)
def change_status(
    lead_id: int, request: Request,
    deal_status: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _ensure_sales_access(request, db)
    lead = _load_lead(db, lead_id)

    if deal_status not in DEAL_STATUS_LABELS:
        raise HTTPException(status_code=400, detail=f"Неизвестный статус: {deal_status}")

    old = lead.deal_status
    if old == deal_status:
        # Ничего не изменилось
        if _is_htmx(request):
            return templates.TemplateResponse(
                request, "partials/status_select.html", {"lead": lead, "saved": False},
            )
        return JSONResponse({"ok": True, "deal_status": deal_status})

    lead.deal_status = deal_status
    lead.updated_at = datetime.utcnow()
    log_activity(
        db, user_id=user.id, action="lead_status_changed",
        lead_id=lead.id, meta={"from": old, "to": deal_status},
    )
    db.commit()
    db.refresh(lead)

    if _is_htmx(request):
        return templates.TemplateResponse(
            request, "partials/status_select.html", {"lead": lead, "saved": True},
        )
    return JSONResponse({"ok": True, "deal_status": lead.deal_status})


@app.patch("/api/leads/{lead_id}/feedback", response_class=HTMLResponse)
def save_feedback(
    lead_id: int, request: Request,
    feedback: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _ensure_sales_access(request, db)
    lead = _load_lead(db, lead_id)

    new_value = (feedback or "").strip() or None
    old_value = lead.feedback

    if old_value == new_value:
        # Нечего сохранять
        if _is_htmx(request):
            return templates.TemplateResponse(
                request, "partials/feedback_saved.html",
                {"saved": False, "timestamp": datetime.utcnow()},
            )
        return JSONResponse({"ok": True, "changed": False})

    lead.feedback = new_value
    lead.updated_at = datetime.utcnow()
    log_activity(
        db, user_id=user.id, action="lead_feedback_changed",
        lead_id=lead.id,
        meta={"length": len(new_value) if new_value else 0},
    )
    db.commit()

    if _is_htmx(request):
        return templates.TemplateResponse(
            request, "partials/feedback_saved.html",
            {"saved": True, "timestamp": datetime.utcnow()},
        )
    return JSONResponse({"ok": True, "changed": True})


@app.patch("/api/leads/{lead_id}/assigned", response_class=HTMLResponse)
def change_assigned(
    lead_id: int, request: Request,
    assigned_to_id: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _ensure_sales_access(request, db)
    lead = _load_lead(db, lead_id)

    new_id = int(assigned_to_id) if assigned_to_id and assigned_to_id.isdigit() else None
    if new_id is not None:
        target = db.get(User, new_id)
        if target is None or not target.is_active:
            raise HTTPException(status_code=400, detail="Юзер не найден")

    old = lead.assigned_to_id
    if old == new_id:
        if _is_htmx(request):
            return templates.TemplateResponse(
                request, "partials/assigned_select.html",
                {"lead": lead, "users": _active_users(db), "saved": False},
            )
        return JSONResponse({"ok": True, "assigned_to_id": new_id})

    lead.assigned_to_id = new_id
    lead.updated_at = datetime.utcnow()
    log_activity(
        db, user_id=user.id, action="lead_assigned",
        lead_id=lead.id, meta={"from": old, "to": new_id},
    )
    db.commit()
    db.refresh(lead)

    if _is_htmx(request):
        return templates.TemplateResponse(
            request, "partials/assigned_select.html",
            {"lead": lead, "users": _active_users(db), "saved": True},
        )
    return JSONResponse({"ok": True, "assigned_to_id": new_id})


def _active_users(db: Session) -> list[User]:
    return list(db.scalars(
        select(User).where(User.is_active == True).order_by(User.username)
    ).all())


@app.get("/partials/lead-card/{lead_id}", response_class=HTMLResponse)
def lead_card_partial(
    lead_id: int, request: Request, db: Session = Depends(get_db),
):
    """Возвращает карточку лида — для апдейта на дашборде без перезагрузки."""
    require_user_dep(request, db)
    lead = _load_lead(db, lead_id)
    return templates.TemplateResponse(
        request, "partials/lead_card.html",
        {"lead": lead, "DEAL_STATUS_LABELS": DEAL_STATUS_LABELS},
    )


# ==== Админ-панель ====

def _require_admin_dep(request: Request, db: Session) -> User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Только для администраторов")
    return user


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not admin:
        return _redirect("/login")
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Только для администраторов")

    users = list(db.scalars(select(User).order_by(User.created_at)).all())
    return _render(request, "admin/users.html", users=users)


@app.post("/admin/users", response_class=HTMLResponse)
def admin_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(ROLE_SALES),
    db: Session = Depends(get_db),
):
    admin = _require_admin_dep(request, db)

    err = validate_username(username) or validate_password(password)
    if err is None and role not in (ROLE_ADMIN, ROLE_SALES):
        err = "Недопустимая роль"
    if err is None and db.scalar(select(User).where(User.username == username.strip())):
        err = "Пользователь с таким именем уже есть"

    users = list(db.scalars(select(User).order_by(User.created_at)).all())

    if err:
        if _is_htmx(request):
            return templates.TemplateResponse(
                request, "admin/_users_form.html",
                {"error": err, "username": username.strip(), "role": role},
            )
        return _render(request, "admin/users.html", users=users,
                       error=err, form_username=username.strip(), form_role=role)

    new_user = User(
        username=username.strip(),
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        created_at=datetime.utcnow(),
        created_by_id=admin.id,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    log_activity(
        db, user_id=admin.id, action="user_created",
        meta={"username": new_user.username, "role": role, "new_user_id": new_user.id},
    )

    if _is_htmx(request):
        # Возвращаем всю таблицу юзеров — дёшево, всегда до 10 записей
        users = list(db.scalars(select(User).order_by(User.created_at)).all())
        return templates.TemplateResponse(
            request, "admin/_users_table.html",
            {"users": users, "user": admin, "flash": f"Юзер «{new_user.username}» создан"},
        )
    return _redirect("/admin/users")


@app.patch("/admin/users/{user_id}/role", response_class=HTMLResponse)
def admin_toggle_role(
    user_id: int, request: Request, db: Session = Depends(get_db),
):
    admin = _require_admin_dep(request, db)
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Юзер не найден")

    old = target.role
    target.role = ROLE_SALES if target.role == ROLE_ADMIN else ROLE_ADMIN

    # Защита: не снимаем последнего активного админа
    if old == ROLE_ADMIN and target.role == ROLE_SALES:
        remaining = db.scalar(
            select(func.count(User.id))
            .where(User.role == ROLE_ADMIN, User.is_active == True, User.id != target.id)
        ) or 0
        if remaining == 0:
            target.role = ROLE_ADMIN  # откатываем
            raise HTTPException(
                status_code=400,
                detail="Нельзя снять роль — это последний активный админ",
            )

    log_activity(
        db, user_id=admin.id, action="user_role_changed",
        meta={"target_id": target.id, "from": old, "to": target.role},
    )
    db.commit()
    db.refresh(target)

    if _is_htmx(request):
        return templates.TemplateResponse(
            request, "admin/_user_row.html",
            {"u": target, "user": admin},
        )
    return _redirect("/admin/users")


@app.patch("/admin/users/{user_id}/active", response_class=HTMLResponse)
def admin_toggle_active(
    user_id: int, request: Request, db: Session = Depends(get_db),
):
    admin = _require_admin_dep(request, db)
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Юзер не найден")

    was_active = target.is_active

    # Защита: не деактивируем последнего админа и не деактивируем себя
    if was_active and target.id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя деактивировать себя")
    if was_active and target.is_admin:
        remaining = db.scalar(
            select(func.count(User.id))
            .where(User.role == ROLE_ADMIN, User.is_active == True, User.id != target.id)
        ) or 0
        if remaining == 0:
            raise HTTPException(
                status_code=400,
                detail="Нельзя деактивировать последнего активного админа",
            )

    target.is_active = not was_active
    log_activity(
        db, user_id=admin.id,
        action="user_deactivated" if was_active else "user_activated",
        meta={"target_id": target.id},
    )
    db.commit()
    db.refresh(target)

    if _is_htmx(request):
        return templates.TemplateResponse(
            request, "admin/_user_row.html",
            {"u": target, "user": admin},
        )
    return _redirect("/admin/users")


@app.get("/admin/stats", response_class=HTMLResponse)
def admin_stats(request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not admin:
        return _redirect("/login")
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Только для администраторов")

    from datetime import timedelta
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    total_leads = db.scalar(select(func.count(ProcessedLead.id))) or 0
    called_total = db.scalar(
        select(func.count(ProcessedLead.id)).where(ProcessedLead.called == True)
    ) or 0
    called_day = db.scalar(
        select(func.count(ProcessedLead.id))
        .where(ProcessedLead.called == True, ProcessedLead.called_at >= day_ago)
    ) or 0
    called_week = db.scalar(
        select(func.count(ProcessedLead.id))
        .where(ProcessedLead.called == True, ProcessedLead.called_at >= week_ago)
    ) or 0
    called_month = db.scalar(
        select(func.count(ProcessedLead.id))
        .where(ProcessedLead.called == True, ProcessedLead.called_at >= month_ago)
    ) or 0

    # Воронка по статусам
    funnel = dict(db.execute(
        select(ProcessedLead.deal_status, func.count(ProcessedLead.id))
        .group_by(ProcessedLead.deal_status)
    ).all())

    # По приоритетам
    by_priority = dict(db.execute(
        select(ProcessedLead.priority, func.count(ProcessedLead.id))
        .group_by(ProcessedLead.priority)
    ).all())

    # По продажникам (кто сколько звонков сделал)
    by_sales = list(db.execute(
        select(
            User.username,
            User.role,
            func.count(ProcessedLead.id).label("calls"),
        )
        .join(ProcessedLead, ProcessedLead.called_by_id == User.id)
        .where(ProcessedLead.called == True)
        .group_by(User.id, User.username, User.role)
        .order_by(func.count(ProcessedLead.id).desc())
    ).all())

    return _render(
        request, "admin/stats.html",
        total_leads=total_leads,
        called_total=called_total,
        called_day=called_day,
        called_week=called_week,
        called_month=called_month,
        funnel=funnel,
        by_priority=by_priority,
        by_sales=by_sales,
    )


@app.get("/admin/activity", response_class=HTMLResponse)
def admin_activity(request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not admin:
        return _redirect("/login")
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Только для администраторов")

    # Последние 200 событий
    rows = list(db.execute(
        select(ActivityLog, User.username)
        .outerjoin(User, ActivityLog.user_id == User.id)
        .order_by(ActivityLog.created_at.desc())
        .limit(200)
    ).all())

    # Для каждой записи можем добавить lead info
    lead_ids = [r[0].lead_id for r in rows if r[0].lead_id]
    leads_map = {}
    if lead_ids:
        for l in db.scalars(
            select(ProcessedLead).where(ProcessedLead.id.in_(lead_ids))
        ).all():
            leads_map[l.id] = l.company_name

    return _render(
        request, "admin/activity.html",
        rows=rows,
        leads_map=leads_map,
    )


# ==== Machine ingest API (для лидогенератора) ====

def _check_ingest_token(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth[7:].strip()
    expected = settings.ingest_token or ""
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="Invalid ingest token")


# ==== Надёжный прокси-fetch (обходит сертификаты/антибот/упавшие сайты) ====

import warnings
try:
    from urllib3.exceptions import InsecureRequestWarning
    warnings.filterwarnings("ignore", category=InsecureRequestWarning)
except Exception:
    pass

_FETCH_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
]
_FETCH_BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    # Убираем br (Brotli) — требует отдельной либы, httpx с ней не всегда дружит
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}
_FETCH_MAX_BYTES = 200_000  # 200 KB — достаточно для анализа


@app.get("/api/fetch")
def api_fetch(url: str, request: Request):
    """Robust HTML-fetch для агента: игнор SSL, ротация User-Agent, Wayback fallback.

    Используется cloud-trigger'ом когда обычный WebFetch упал.

    Параметры:
      url — полный URL страницы
    Ответ: {ok, html, final_url, status, source, ua_used, error?}
    """
    import httpx

    _check_ingest_token(request)

    if not url or not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http(s)://")
    if len(url) > 2048:
        raise HTTPException(status_code=400, detail="url too long")

    last_err = "all attempts failed"

    # 1) Три попытки с разными UA, игнор SSL
    for i, ua in enumerate(_FETCH_UAS):
        try:
            with httpx.Client(
                verify=False,
                follow_redirects=True,
                timeout=httpx.Timeout(connect=8.0, read=15.0, write=10.0, pool=5.0),
                headers={**_FETCH_BASE_HEADERS, "User-Agent": ua},
            ) as client:
                r = client.get(url)
                content_type = r.headers.get("content-type", "").lower()
                if r.status_code == 200 and ("text/html" in content_type or "text/xml" in content_type or "application/xhtml" in content_type or not content_type):
                    # Режем по байтам, кодируем через r.text (уже декодировано httpx)
                    html = r.text
                    if len(html) > _FETCH_MAX_BYTES:
                        html = html[:_FETCH_MAX_BYTES]
                    return {
                        "ok": True,
                        "html": html,
                        "final_url": str(r.url),
                        "status": r.status_code,
                        "source": "direct",
                        "ua_used": ["chrome", "safari", "firefox"][i],
                    }
                last_err = f"http {r.status_code} ({content_type[:40]})"
        except httpx.TimeoutException as e:
            last_err = f"timeout: {type(e).__name__}"
        except httpx.ConnectError as e:
            last_err = f"connect error: {str(e)[:100]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:100]}"

    # 2) Wayback Machine fallback
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            wb = client.get(
                "http://archive.org/wayback/available",
                params={"url": url},
            )
            snap = (wb.json().get("archived_snapshots") or {}).get("closest") or {}
            snap_url = snap.get("url")
            if snap_url:
                r = client.get(
                    snap_url,
                    headers={**_FETCH_BASE_HEADERS, "User-Agent": _FETCH_UAS[0]},
                    timeout=20.0,
                )
                if r.status_code == 200:
                    html = r.text
                    if len(html) > _FETCH_MAX_BYTES:
                        html = html[:_FETCH_MAX_BYTES]
                    return {
                        "ok": True,
                        "html": html,
                        "final_url": snap_url,
                        "status": r.status_code,
                        "source": "wayback",
                        "archive_timestamp": snap.get("timestamp"),
                    }
    except Exception as e:
        last_err = f"wayback failed: {str(e)[:80]}; direct: {last_err}"

    return JSONResponse(
        {"ok": False, "error": last_err, "url": url},
        status_code=200,  # возвращаем 200 с ok=False чтобы агент не обрабатывал как исключение
    )


# ==== GET-версия ingest для WebFetch (sandbox не пускает POST/curl) ====

@app.get("/api/leads/import-get", response_class=JSONResponse)
def api_import_via_get(
    d: str, t: str, request: Request, db: Session = Depends(get_db),
):
    """GET-версия импорта: JSON в base64-url, token в query.
    Для клиентов которые могут только GET (Anthropic WebFetch)."""
    import base64

    expected = settings.ingest_token or ""
    if not expected or t != expected:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        # base64url + автопаддинг
        decoded = base64.urlsafe_b64decode(d + "=" * (-len(d) % 4)).decode("utf-8")
        data = json.loads(decoded)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"bad base64/json: {e}")

    # Валидация
    missing = [f for f in REQUIRED_IMPORT_FIELDS if f not in data]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {missing}")
    priority = int(data["priority"])
    if priority not in (1, 2, 3, 4, 5):
        raise HTTPException(status_code=400, detail="priority must be 1..5")
    pains = data["pains"]
    services = data["recommended_services"]
    if not isinstance(pains, list) or not pains:
        raise HTTPException(status_code=400, detail="pains must be non-empty list")
    if not isinstance(services, list) or not services:
        raise HTTPException(status_code=400, detail="recommended_services must be non-empty list")

    from pipeline.save_analyzed import _dedup_key, _normalize_url

    company_name = str(data["company_name"]).strip()
    website_url = data.get("website_url")
    dedup_key = _dedup_key(company_name, website_url)

    existing = db.scalar(select(ProcessedLead).where(ProcessedLead.dedup_key == dedup_key))
    if existing:
        return {"skipped": True, "reason": "duplicate",
                "existing_id": existing.id, "priority": existing.priority}

    country = (data.get("country") or "RU").strip().upper()[:2]
    if not country.isalpha():
        country = "RU"
    upsell_offers = data.get("upsell_offers") if isinstance(data.get("upsell_offers"), list) else None
    talking_points = data.get("talking_points") if isinstance(data.get("talking_points"), list) else None
    objections = data.get("objections") if isinstance(data.get("objections"), list) else None

    lead = ProcessedLead(
        dedup_key=dedup_key,
        company_name=company_name,
        website_url=_normalize_url(website_url) if website_url else None,
        phone=data.get("phone"),
        city=data.get("city"),
        country=country,
        industry=data.get("industry"),
        website_status=data.get("website_status"),
        summary=data.get("summary"),
        pains=[str(p) for p in pains],
        recommended_services=[str(s) for s in services],
        sales_hook=data.get("sales_hook"),
        priority=priority,
        priority_reason=data.get("priority_reason"),
        upsell_offers=upsell_offers,
        talking_points=[str(t) for t in talking_points] if talking_points else None,
        objections=objections,
        analyzed_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        deal_status="new",
        called=False,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    log_activity(db, user_id=None, action="lead_imported",
                 lead_id=lead.id,
                 meta={"company": company_name, "priority": priority, "via": "get"})
    return {"ok": True, "id": lead.id, "priority": priority, "company_name": company_name}


@app.get("/api/leads/check-dup-get")
def api_check_dup_get(key: str, t: str, db: Session = Depends(get_db)):
    """GET-версия check-dup с токеном в query (для WebFetch)."""
    expected = settings.ingest_token or ""
    if not expected or t != expected:
        raise HTTPException(status_code=401, detail="Invalid token")
    existing = db.scalar(select(ProcessedLead).where(ProcessedLead.dedup_key == key))
    if existing:
        return {"duplicate": True, "id": existing.id,
                "company_name": existing.company_name, "priority": existing.priority}
    return {"duplicate": False, "dedup_key": key}


@app.get("/api/leads/check-dup")
def api_check_dup(key: str, request: Request, db: Session = Depends(get_db)):
    _check_ingest_token(request)
    existing = db.scalar(select(ProcessedLead).where(ProcessedLead.dedup_key == key))
    if existing:
        return {
            "duplicate": True,
            "id": existing.id,
            "company_name": existing.company_name,
            "sheet_row": existing.sheet_row,
            "priority": existing.priority,
        }
    return {"duplicate": False, "dedup_key": key}


REQUIRED_IMPORT_FIELDS = (
    "company_name", "website_status", "summary", "pains",
    "recommended_services", "sales_hook", "priority", "priority_reason",
)


@app.post("/api/leads/import")
async def api_import_lead(request: Request, db: Session = Depends(get_db)):
    _check_ingest_token(request)

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    missing = [f for f in REQUIRED_IMPORT_FIELDS if f not in data]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {missing}")

    priority = int(data["priority"])
    if priority not in (1, 2, 3, 4, 5):
        raise HTTPException(status_code=400, detail="priority must be 1..5")

    pains = data["pains"]
    services = data["recommended_services"]
    if not isinstance(pains, list) or not pains:
        raise HTTPException(status_code=400, detail="'pains' must be non-empty list")
    if not isinstance(services, list) or not services:
        raise HTTPException(status_code=400, detail="'recommended_services' must be non-empty list")

    # Импортируем dedup-хелпер
    from pipeline.save_analyzed import _dedup_key, _normalize_url

    company_name = str(data["company_name"]).strip()
    website_url = data.get("website_url")
    dedup_key = _dedup_key(company_name, website_url)

    existing = db.scalar(select(ProcessedLead).where(ProcessedLead.dedup_key == dedup_key))
    if existing:
        return {
            "skipped": True, "reason": "duplicate",
            "existing_id": existing.id, "priority": existing.priority,
        }

    # Опциональные расширенные поля (если агент их прислал)
    upsell_offers = data.get("upsell_offers") or None
    talking_points = data.get("talking_points") or None
    objections = data.get("objections") or None
    # Мягкая валидация: должны быть списками
    if upsell_offers is not None and not isinstance(upsell_offers, list):
        upsell_offers = None
    if talking_points is not None and not isinstance(talking_points, list):
        talking_points = None
    if objections is not None and not isinstance(objections, list):
        objections = None

    # Страна — ISO-2, default RU. Принимаем любой регистр.
    country = (data.get("country") or "RU").strip().upper()[:2]
    if not country.isalpha():
        country = "RU"

    lead = ProcessedLead(
        dedup_key=dedup_key,
        company_name=company_name,
        website_url=_normalize_url(website_url) if website_url else None,
        phone=data.get("phone"),
        city=data.get("city"),
        country=country,
        industry=data.get("industry"),
        website_status=data.get("website_status"),
        summary=data.get("summary"),
        pains=[str(p) for p in pains],
        recommended_services=[str(s) for s in services],
        sales_hook=data.get("sales_hook"),
        priority=priority,
        priority_reason=data.get("priority_reason"),
        upsell_offers=upsell_offers,
        talking_points=[str(t) for t in talking_points] if talking_points else None,
        objections=objections,
        analyzed_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        deal_status="new",
        called=False,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    log_activity(
        db, user_id=None, action="lead_imported",
        lead_id=lead.id,
        meta={"company": company_name, "priority": priority},
    )

    return {
        "ok": True, "id": lead.id, "priority": priority,
        "company_name": company_name, "dedup_key": dedup_key,
    }
