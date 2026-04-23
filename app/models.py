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
