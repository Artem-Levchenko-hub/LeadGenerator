"""Модели SQLite для веб-приложения Stenvik Leads.

Структура:
- User — продажники и админы (роли admin | sales)
- ProcessedLead — лиды: аналитика + CRM-поля, которые меняют продажники
- ActivityLog — лог действий (для админки: кто-что-когда)
- RunLog — лог тиков агента лидогенератора
"""
from datetime import datetime
from sqlalchemy import (
    String, Integer, Text, DateTime, Boolean, Index, JSON, ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


# ==== Роли и статусы — константы ====

ROLE_ADMIN = "admin"
ROLE_SALES = "sales"
ROLES = (ROLE_ADMIN, ROLE_SALES)

DEAL_STATUS_NEW = "new"
DEAL_STATUS_IN_WORK = "in_work"
DEAL_STATUS_CONTACTED = "contacted"
DEAL_STATUS_QUALIFIED = "qualified"
DEAL_STATUS_DEAL = "deal"
DEAL_STATUS_REJECTED = "rejected"
DEAL_STATUS_NOT_OURS = "not_ours"

DEAL_STATUS_LABELS = {
    DEAL_STATUS_NEW:        "Новый",
    DEAL_STATUS_IN_WORK:    "В работе",
    DEAL_STATUS_CONTACTED:  "Связались",
    DEAL_STATUS_QUALIFIED:  "Квалифицирован",
    DEAL_STATUS_DEAL:       "Сделка",
    DEAL_STATUS_REJECTED:   "Отказ",
    DEAL_STATUS_NOT_OURS:   "Не наш",
}

DEAL_STATUS_ORDER = list(DEAL_STATUS_LABELS.keys())


# ==== Модели ====

class User(Base):
    """Пользователь веб-приложения (продажник или админ).

    Bootstrap: первый зарегистрировавшийся юзер автоматически получает role=admin.
    Дальше регистрация только через админ-панель.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=ROLE_SALES, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Обратные связи
    called_leads: Mapped[list["ProcessedLead"]] = relationship(
        "ProcessedLead",
        back_populates="called_by",
        foreign_keys="ProcessedLead.called_by_id",
    )
    assigned_leads: Mapped[list["ProcessedLead"]] = relationship(
        "ProcessedLead",
        back_populates="assigned_to",
        foreign_keys="ProcessedLead.assigned_to_id",
    )
    activity: Mapped[list["ActivityLog"]] = relationship(
        "ActivityLog", back_populates="user",
    )

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class ProcessedLead(Base):
    """Лид: результат анализа агентом + CRM-поля, которые меняют продажники.

    Поля analysis_* приходят от агента (неизменны).
    Поля crm_* меняет продажник в веб-интерфейсе.
    """
    __tablename__ = "processed_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)

    # === Что прислал агент (аналитика) ===
    company_name: Mapped[str] = mapped_column(String(512))
    website_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country: Mapped[str] = mapped_column(String(2), default="RU", index=True)  # ISO-2: RU/US/KZ/BY...
    industry: Mapped[str | None] = mapped_column(String(256), nullable=True)
    website_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    pains: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recommended_services: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sales_hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    priority_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Расширенные поля для продажника (апсейлы, скрипт звонка, возражения)
    upsell_offers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    talking_points: Mapped[list | None] = mapped_column(JSON, nullable=True)
    objections: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # === Системные ===
    sheet_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    md_public_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True,
    )

    # === CRM-поля (продажник меняет) ===
    called: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    called_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    called_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True,
    )
    deal_status: Mapped[str] = mapped_column(
        String(32), default=DEAL_STATUS_NEW, index=True,
    )
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
    )

    # Связи
    called_by: Mapped["User | None"] = relationship(
        "User", back_populates="called_leads", foreign_keys=[called_by_id],
    )
    assigned_to: Mapped["User | None"] = relationship(
        "User", back_populates="assigned_leads", foreign_keys=[assigned_to_id],
    )

    __table_args__ = (
        Index("idx_analyzed_priority", "analyzed_at", "priority"),
        Index("idx_deal_status_priority", "deal_status", "priority"),
    )


class ActivityLog(Base):
    """Лог действий пользователей. Показывается в админке.

    Примеры action:
      login, logout, register, user_created, user_role_changed, user_deactivated,
      lead_called, lead_uncalled, lead_status_changed, lead_feedback_changed,
      lead_assigned.
    """
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True,
    )
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("processed_leads.id"), nullable=True, index=True,
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True,
    )

    user: Mapped["User | None"] = relationship("User", back_populates="activity")

    __table_args__ = (
        Index("idx_activity_created_user", "created_at", "user_id"),
    )


class RunLog(Base):
    """Лог тиков агента /loop — сколько нашёл, сколько сохранил, ошибки."""
    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    leads_created: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)


# ============================================================
# === Полный жизненный цикл студии — агентная архитектура ===
# ============================================================
# Эти таблицы добавлены поверх существующих (User/ProcessedLead/...)
# для поддержки автономной агентной системы. См. план
# ~/.claude/plans/lovely-dazzling-cat.md и memory/project_lead_generation.md.
#
# State machine компании (Company.stage):
#   prospect → contacted → engaged → qualified → discovery →
#   requirements → estimated → proposal_sent → negotiation →
#   contract → kickoff → in_development → delivered →
#   support_active → churned
# ============================================================


# === Стадии воронки (полный цикл студии) ===
STAGE_PROSPECT        = "prospect"          # лид найден, ещё не касались
STAGE_CONTACTED       = "contacted"         # первое касание ушло
STAGE_ENGAGED         = "engaged"           # клиент ответил
STAGE_QUALIFIED       = "qualified"         # BANT пройден (есть бюджет/лпр/срок)
STAGE_DISCOVERY       = "discovery"         # сбор контекста бизнеса
STAGE_REQUIREMENTS    = "requirements"      # сбор ТЗ
STAGE_ESTIMATED       = "estimated"         # смета готова
STAGE_PROPOSAL_SENT   = "proposal_sent"     # КП отправлено
STAGE_NEGOTIATION     = "negotiation"       # переговоры/правки
STAGE_CONTRACT        = "contract"          # договор подписан
STAGE_KICKOFF         = "kickoff"           # старт проекта
STAGE_IN_DEVELOPMENT  = "in_development"    # ведётся разработка
STAGE_DELIVERED       = "delivered"         # сдан
STAGE_SUPPORT_ACTIVE  = "support_active"    # в поддержке
STAGE_CHURNED         = "churned"           # ушёл/отказался

STAGE_LABELS = {
    STAGE_PROSPECT:        "Найден",
    STAGE_CONTACTED:       "Касание отправлено",
    STAGE_ENGAGED:         "Откликнулся",
    STAGE_QUALIFIED:       "Квалифицирован",
    STAGE_DISCOVERY:       "Дискавери",
    STAGE_REQUIREMENTS:    "Сбор ТЗ",
    STAGE_ESTIMATED:       "Смета готова",
    STAGE_PROPOSAL_SENT:   "КП отправлено",
    STAGE_NEGOTIATION:     "Переговоры",
    STAGE_CONTRACT:        "Договор",
    STAGE_KICKOFF:         "Kickoff",
    STAGE_IN_DEVELOPMENT:  "В разработке",
    STAGE_DELIVERED:       "Сдан",
    STAGE_SUPPORT_ACTIVE:  "Поддержка",
    STAGE_CHURNED:         "Ушёл/отказ",
}
STAGE_ORDER = list(STAGE_LABELS.keys())


# === Каналы исходящей связи ===
CHANNEL_EMAIL    = "email"
CHANNEL_TELEGRAM = "telegram"
CHANNEL_SMS      = "sms"
CHANNEL_CALL     = "call"
CHANNELS = (CHANNEL_EMAIL, CHANNEL_TELEGRAM, CHANNEL_SMS, CHANNEL_CALL)


# === Статусы исходящего сообщения ===
OUTBOX_DRAFT     = "draft"      # агент написал, не одобрено Auditor'ом
OUTBOX_HOLDING   = "holding"    # одобрено, ждёт холодильник send_after
OUTBOX_APPROVED  = "approved"   # alias для holding (исторически)
OUTBOX_SENDING   = "sending"    # сейчас отправляется
OUTBOX_SENT      = "sent"
OUTBOX_FAILED    = "failed"
OUTBOX_REJECTED  = "rejected"   # Auditor не пропустил
OUTBOX_CANCELLED = "cancelled"  # отозвано до отправки


# === Состояния треда диалога ===
CONV_NEW                = "new"
CONV_ENGAGED            = "engaged"
CONV_QUALIFYING         = "qualifying"
CONV_READY_FOR_PROPOSAL = "ready_for_proposal"
CONV_NEGOTIATING        = "negotiating"
CONV_WON                = "won"
CONV_LOST               = "lost"
CONV_STALLED            = "stalled"
CONV_NEEDS_HUMAN        = "needs_human"


# === Виды agent_tasks ===
TASK_HUNT             = "hunt"
TASK_OUTREACH_FIRST   = "outreach.first_touch"
TASK_OUTREACH_CONT    = "outreach.continue"
TASK_SALES_CONT       = "sales.continue"
TASK_DISCOVERY_CONT   = "discovery.continue"
TASK_REQUIREMENTS_CONT = "requirements.continue"
TASK_ANALYST_RUN      = "analyst.run"
TASK_ESTIMATE         = "estimate.run"
TASK_PROPOSAL         = "proposal.render"
TASK_SUPPORT_REPLY    = "support.reply"
TASK_OUTBOX_FLUSH     = "outbox.flush_due"
TASK_INBOX_POLL       = "inbox.poll"
TASK_STRATEGIC_REVIEW = "strategic.review"

# === Агенты (для логирования) ===
AGENT_HUNTER       = "hunter"
AGENT_OUTREACH     = "outreach"
AGENT_SALES        = "sales"
AGENT_ANALYST      = "analyst"
AGENT_DISCOVERY    = "discovery"
AGENT_REQUIREMENTS = "requirements"
AGENT_ESTIMATION   = "estimation"
AGENT_PROPOSAL     = "proposal"
AGENT_SUPPORT      = "support"
AGENT_AUDITOR      = "auditor"
AGENT_STRATEGIC    = "strategic"


class Company(Base):
    """Главная full-cycle сущность.

    Создаётся из ProcessedLead на этапе перехода 'prospect → contacted' и далее
    проходит весь жизненный цикл. ProcessedLead остаётся как «raw lead»
    (что нашёл Hunter); Company — это «работающая с нами» компания.
    """
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("processed_leads.id"), nullable=True, index=True,
    )
    name: Mapped[str] = mapped_column(String(512), index=True)
    website_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    country: Mapped[str] = mapped_column(String(2), default="RU")
    contacts: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {email, phone, tg, ...}
    stage: Mapped[str] = mapped_column(
        String(32), default=STAGE_PROSPECT, index=True,
    )
    assigned_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True,
    )
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_stage_change_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
    )

    __table_args__ = (
        Index("idx_company_stage_changed", "stage", "last_stage_change_at"),
    )


class StageHistory(Base):
    """Таймлайн смены стадий компании. Пишется Project Tracker'ом
    (сервис в Tactical Orchestrator) на каждое изменение Company.stage.
    """
    __tablename__ = "stage_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), index=True,
    )
    from_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_stage: Mapped[str] = mapped_column(String(32), index=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True,
    )
    changed_by_agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    changed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentTask(Base):
    """Очередь работы агентов. Tactical Orchestrator кладёт сюда задачи,
    воркер достаёт по приоритету и запускает соответствующего агента.
    """
    __tablename__ = "agent_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True,
    )  # pending|running|done|failed|cancelled
    priority: Mapped[int] = mapped_column(Integer, default=5, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True,
    )
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("processed_leads.id"), nullable=True, index=True,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True, index=True,
    )

    __table_args__ = (
        Index("idx_task_pending", "status", "scheduled_at", "priority"),
    )


class AgentRun(Base):
    """Лог одного запуска агента: модель, токены, стоимость, успех/ошибка.
    Это база для дашборда стоимости LLM и аудита поведения агентов.
    """
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_kind: Mapped[str] = mapped_column(String(32), index=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_tasks.id"), nullable=True, index=True,
    )
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class Conversation(Base):
    """Тред диалога с лидом по одному каналу."""
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), index=True,
    )
    channel: Mapped[str] = mapped_column(String(16), index=True)
    external_thread_id: Mapped[str | None] = mapped_column(
        String(512), nullable=True, index=True,
    )  # Message-ID первой ветки / chat_id TG
    state: Mapped[str] = mapped_column(
        String(32), default=CONV_NEW, index=True,
    )
    bot_messages_count: Mapped[int] = mapped_column(Integer, default=0)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Message(Base):
    """Реплика треда — входящая или исходящая."""
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), index=True,
    )
    direction: Mapped[str] = mapped_column(String(8), index=True)  # in|out
    outbox_id: Mapped[int | None] = mapped_column(
        ForeignKey("outbox_messages.id"), nullable=True,
    )
    body_text: Mapped[str] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sender: Mapped[str | None] = mapped_column(String(256), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True,
    )
    llm_used: Mapped[str | None] = mapped_column(String(64), nullable=True)


class OutboxMessage(Base):
    """Все исходящие. Никакой агент НЕ шлёт напрямую — только через outbox.
    Auditor одобряет → status=holding → через холодильник send_after уходит.
    """
    __tablename__ = "outbox_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True, index=True,
    )
    channel: Mapped[str] = mapped_column(String(16), index=True)
    to_address: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body_text: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=OUTBOX_DRAFT, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True,
    )
    send_after: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    audit_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recall_token: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True,
    )
    created_by_agent: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        Index("idx_outbox_status_send_after", "status", "send_after"),
    )


class LeadWeakness(Base):
    """Слабые места сайта/бизнеса лида, найденные Outreach Agent'ом
    через tools fetch_site/dns_check/whois. Используются в первом касании.
    """
    __tablename__ = "lead_weaknesses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), index=True,
    )
    kind: Mapped[str] = mapped_column(String(64), index=True)  # no_https, slow_mobile, ...
    severity: Mapped[str] = mapped_column(String(8), default="med")  # low|med|high
    evidence_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    observation_text: Mapped[str] = mapped_column(Text)  # факт в стиле наблюдения
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    est_effort: Mapped[str | None] = mapped_column(String(64), nullable=True)
    est_impact: Mapped[str | None] = mapped_column(Text, nullable=True)  # bottom-line impact
    found_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Template(Base):
    """Версионированные шаблоны сообщений. CRUD через дашборд."""
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    body_md: Mapped[str] = mapped_column(Text)
    variables: Mapped[list | None] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class KillSwitch(Base):
    """Глобальный стоп. Всегда одна строка с id=1."""
    __tablename__ = "kill_switch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    state: Mapped[str] = mapped_column(
        String(32), default="running",
    )  # running | paused_manual | paused_budget | paused_audit
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    set_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True,
    )
    set_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DailyQuota(Base):
    """Дневные счётчики и лимиты по каналам и LLM."""
    __tablename__ = "daily_quotas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    channel: Mapped[str] = mapped_column(String(16), index=True)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    limit_count: Mapped[int] = mapped_column(Integer, default=0)
    llm_cost_usd: Mapped[float] = mapped_column(default=0.0)

    __table_args__ = (
        Index("idx_quota_date_channel", "date", "channel", unique=True),
    )


class Blacklist(Base):
    """Контакты, которым нельзя писать (отписки, отказы, ручные баны)."""
    __tablename__ = "blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # email|domain|phone|tg
    value: Mapped[str] = mapped_column(String(512), index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IndustryInsight(Base):
    """Кэш анализа отрасли клиента, собирается Industry Analyst Agent'ом.
    Переиспользуется между компаниями той же industry+city в окне ttl_days.
    """
    __tablename__ = "industry_insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True,
    )
    industry: Mapped[str] = mapped_column(String(128), index=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))  # competitor, kpi, trend, regulatory, ...
    text: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    found_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True,
    )
    ttl_days: Mapped[int] = mapped_column(Integer, default=30)


class DiscoveryFinding(Base):
    """Записи Discovery Agent'а — что узнал про бизнес клиента."""
    __tablename__ = "discovery_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), index=True,
    )
    area: Mapped[str] = mapped_column(String(32), index=True)
    # area ∈ business | users | processes | kpis | systems | pains
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    importance: Mapped[str] = mapped_column(String(8), default="med")  # low|med|high
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Requirement(Base):
    """Функциональные/нефункциональные требования, собранные Requirements Engineer'ом."""
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), index=True,
    )
    type: Mapped[str] = mapped_column(String(32), index=True)
    # functional | nonfunctional | integration | constraint
    priority: Mapped[str] = mapped_column(String(8), default="should")  # must|should|could
    text: Mapped[str] = mapped_column(Text)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|confirmed|changed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Estimation(Base):
    """Смета и оценка работ. Делает Estimation Agent (Opus 4.7)."""
    __tablename__ = "estimations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), index=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    total_hours: Mapped[float] = mapped_column(default=0.0)
    total_price_rub: Mapped[float] = mapped_column(default=0.0)
    breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    packages: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Lite/Standard/Premium
    risks_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    assumptions_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Proposal(Base):
    """Версии КП. Содержат и контекст рендера (для регенерации), и финальный PDF."""
    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), index=True,
    )
    estimation_id: Mapped[int | None] = mapped_column(
        ForeignKey("estimations.id"), nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    content_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sent_outbox_id: Mapped[int | None] = mapped_column(
        ForeignKey("outbox_messages.id"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Case(Base):
    """Портфолио студии — кейсы, используются в КП и outreach.
    restrictions_text — критично для innertalk: «не упоминать шифрование».
    """
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    services: Mapped[list | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    metrics_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    restrictions_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Qualification(Base):
    """BANT и quick-факты квалификации, ведёт Sales Manager Agent."""
    __tablename__ = "qualifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), index=True,
    )
    budget_band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    has_decision_maker: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    timeline: Mapped[str | None] = mapped_column(String(64), nullable=True)
    urgency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ObjectionsLog(Base):
    """История возражений и ответов на них — для будущего обучения playbook'а."""
    __tablename__ = "objections_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), index=True,
    )
    kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    response_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)  # accepted/escalated/lost
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StrategyProposal(Base):
    """Предложения Strategic Orchestrator (Opus, daily) на ревью владельца."""
    __tablename__ = "strategy_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    # new_source | ab_test | priority_shift | directive
    payload: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending",
    )  # pending | approved | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True,
    )


class StrategyDirective(Base):
    """Одобренные директивы — читает Tactical Orchestrator при принятии решений."""
    __tablename__ = "strategy_directives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_proposals.id"), nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    active_from: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    active_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_status: Mapped[str] = mapped_column(String(16), default="active")
