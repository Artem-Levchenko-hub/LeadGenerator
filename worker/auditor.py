"""Auditor — детерминированная safety-стенка.

Не агент, не LLM. Чистые Python-правила. Перед каждой отправкой outbox-
сообщения вызывается `audit(outbox_message)` → AuditResult с allowed/reason.

КРИТИЧЕСКОЕ правило #9 — INNERTALK_NO_ENCRYPTION_GUARD: блокирует любое
сообщение, где упоминание `innertalk` соседствует с упоминанием шифрования.
Это enforced даже если LLM-агент случайно нарушил INNERTALK_RULES в промпте.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app import models
from app.config import settings


# Паттерны, которые нельзя оставлять в production-сообщении.
PLACEHOLDER_PATTERNS = [
    re.compile(r"\{\{[^}]+\}\}"),                  # {{name}}
    re.compile(r"\[INSERT_[A-Z_]+\]"),             # [INSERT_PRICE]
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bXXX\b"),
    re.compile(r"<<[^>]+>>"),                      # <<placeholder>>
]

INNERTALK_NAME = re.compile(r"\binnertalk\b", re.IGNORECASE)
ENCRYPTION_TERMS = re.compile(
    r"(зашифр|шифров|шифр[^о]|encrypt|cipher|"
    r"e2e|end[-\s]?to[-\s]?end)",
    re.IGNORECASE,
)


@dataclass
class AuditResult:
    allowed: bool
    rule: str | None = None
    reason: str | None = None

    @property
    def status(self) -> str:
        return "approved" if self.allowed else "rejected"

    @classmethod
    def ok(cls) -> "AuditResult":
        return cls(allowed=True)

    @classmethod
    def reject(cls, rule: str, reason: str) -> "AuditResult":
        return cls(allowed=False, rule=rule, reason=reason)


def _today_str() -> str:
    return date.today().isoformat()


def audit(db: Session, message: models.OutboxMessage) -> AuditResult:
    """Прогоняет outbox-сообщение через все правила. Возвращает AuditResult.

    Не меняет message.status сама — это делает вызывающий код.
    """
    body = (message.body_text or "")
    body_low = body.lower()

    # Rule 1: kill_switch
    ks = db.query(models.KillSwitch).filter_by(id=1).one_or_none()
    if ks and ks.state != "running":
        return AuditResult.reject(
            "kill_switch",
            f"kill_switch is in state '{ks.state}': {ks.reason or ''}",
        )

    # Rule 2: length
    if len(body) < 50:
        return AuditResult.reject("length", f"body too short: {len(body)} chars (min 50)")
    if len(body) > 4000:
        return AuditResult.reject("length", f"body too long: {len(body)} chars (max 4000)")

    # Rule 3: placeholders
    for pat in PLACEHOLDER_PATTERNS:
        m = pat.search(body)
        if m:
            return AuditResult.reject(
                "placeholders",
                f"unfilled placeholder: '{m.group(0)}'",
            )

    # Rule 4: company name in first 100 chars (если company известна)
    if message.company_id:
        company = db.query(models.Company).filter_by(id=message.company_id).one_or_none()
        if company and company.name:
            head = body[:200].lower()
            # Считаем, что хотя бы первая значимая часть имени должна встретиться.
            words = [w for w in company.name.lower().split() if len(w) >= 3]
            if words and not any(w in head for w in words):
                return AuditResult.reject(
                    "company_name_in_head",
                    f"company name '{company.name}' not in first 200 chars",
                )

    # Rule 5+6: signature + opt-out (только email)
    if message.channel == models.CHANNEL_EMAIL:
        if "stenvik" not in body_low:
            return AuditResult.reject(
                "signature_present",
                "no 'stenvik' or 'stenvik.studio' in body — signature missing",
            )
        if "unsubscribe" not in body_low and "отпис" not in body_low:
            return AuditResult.reject(
                "opt_out_present",
                "no opt-out instruction in body (152-ФЗ)",
            )

    # Rule 7: blacklist
    bl = db.query(models.Blacklist).filter(
        models.Blacklist.value == message.to_address,
    ).first()
    if bl:
        return AuditResult.reject(
            "blacklist",
            f"recipient in blacklist: {bl.reason or bl.kind}",
        )
    # Доменный blacklist для email
    if message.channel == models.CHANNEL_EMAIL and "@" in (message.to_address or ""):
        domain = message.to_address.split("@", 1)[1].lower()
        bl_dom = db.query(models.Blacklist).filter_by(
            kind="domain", value=domain,
        ).first()
        if bl_dom:
            return AuditResult.reject(
                "blacklist_domain",
                f"recipient domain blacklisted: {domain}",
            )

    # Rule 8: daily quota
    quota = db.query(models.DailyQuota).filter_by(
        date=_today_str(), channel=message.channel,
    ).one_or_none()
    limit = _channel_limit(message.channel)
    sent = quota.sent_count if quota else 0
    if sent >= limit:
        # Авто-выставляем kill_switch=paused_budget
        if ks and ks.state == "running":
            ks.state = "paused_budget"
            ks.reason = f"daily limit for {message.channel} reached: {sent}/{limit}"
            db.add(ks)
            db.commit()
        return AuditResult.reject(
            "daily_quota",
            f"daily limit for {message.channel} reached: {sent}/{limit}",
        )

    # Rule 9: 🚫 INNERTALK_NO_ENCRYPTION_GUARD
    if INNERTALK_NAME.search(body) and ENCRYPTION_TERMS.search(body):
        return AuditResult.reject(
            "innertalk_no_encryption",
            "innertalk.space mentioned together with encryption claim — "
            "forbidden by product rule",
        )

    # Rule 10: loop guard
    if message.conversation_id:
        conv = db.query(models.Conversation).filter_by(
            id=message.conversation_id,
        ).one_or_none()
        if conv and conv.bot_messages_count >= settings.conversation_loop_guard_msgs:
            conv.state = models.CONV_NEEDS_HUMAN
            db.add(conv)
            db.commit()
            return AuditResult.reject(
                "loop_guard",
                f"conversation already has {conv.bot_messages_count} bot msgs — "
                f"escalated to human",
            )

    return AuditResult.ok()


def _channel_limit(channel: str) -> int:
    return {
        models.CHANNEL_EMAIL:    settings.daily_email_limit,
        models.CHANNEL_TELEGRAM: settings.daily_telegram_limit,
        models.CHANNEL_SMS:      settings.daily_sms_limit,
        models.CHANNEL_CALL:     settings.daily_call_limit,
    }.get(channel, 0)
