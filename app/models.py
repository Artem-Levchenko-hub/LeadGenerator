from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime, Float, JSON, Index, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hh_employer_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    company_name: Mapped[str] = mapped_column(String(512), index=True)
    industry: Mapped[str | None] = mapped_column(String(256), nullable=True)
    industry_ids: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description_hh: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)

    website_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    website_status: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    website_text_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)

    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_pains: Mapped[list | None] = mapped_column(JSON, nullable=True)
    ai_hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_recommended_services: Mapped[list | None] = mapped_column(JSON, nullable=True)
    ai_priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    ai_priority_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    contact_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    assigned_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("idx_priority_status", "ai_priority", "status"),
    )


class RunLog(Base):
    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    companies_fetched: Mapped[int] = mapped_column(Integer, default=0)
    leads_created: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
