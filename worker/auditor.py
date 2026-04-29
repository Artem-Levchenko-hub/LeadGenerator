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

# Personal-email домены — на них холодный B2B-outreach НЕ идёт.
# Это требование: связываться только по корпоративной почте (info@company.ru,
# contact@example.com и т.п.), а не на личные ящики директоров/сотрудников.
# Personal-адреса попадают в blacklist соображений 152-ФЗ (нет согласия на
# холодную рассылку на личный email физлица).
PERSONAL_EMAIL_DOMAINS = {
    # Россия
    "mail.ru", "list.ru", "bk.ru", "inbox.ru", "internet.ru",
    "yandex.ru", "yandex.com", "yandex.by", "yandex.kz", "ya.ru",
    "rambler.ru", "lenta.ru", "ro.ru", "rambler.ua",
    # Глобальные публичные
    "gmail.com", "googlemail.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "yahoo.co.uk", "ymail.com", "rocketmail.com",
    "icloud.com", "me.com", "mac.com",
    "proton.me", "protonmail.com", "pm.me",
    "aol.com", "gmx.com", "gmx.de", "gmx.net",
    "fastmail.com", "tutanota.com", "tuta.io", "tutamail.com",
    "qq.com", "163.com", "126.com", "sina.com", "sohu.com",
    # СНГ публичные
    "i.ua", "ukr.net", "meta.ua",
    "tut.by", "mail.by",
}


def is_personal_email(address: str) -> bool:
    """True если address на персональном домене (не B2B)."""
    if not address or "@" not in address:
        return False
    domain = address.rsplit("@", 1)[1].lower().strip()
    return domain in PERSONAL_EMAIL_DOMAINS

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

    # Rule 4: company identity in head — name OR website domain должны
    # появиться в первых 300 chars. 2GIS даёт длинные названия типа
    # "Претор, компания по транспортировке больных и вызову врача на дом" —
    # реальное имя только до первой запятой ("Претор"). Также проверяем
    # домен сайта (часто появляется в "Зашёл на сайт pretor.clinic").
    if message.company_id:
        company = db.query(models.Company).filter_by(id=message.company_id).one_or_none()
        if company and company.name:
            head = body[:300].lower()
            # Имя до первой запятой, минус оргформы
            primary = company.name.split(",")[0].strip().lower()
            orgforms = {"ооо", "ип", "гбуз", "фгбу", "фгбоу", "фгуп", "зао", "оао", "пао", "нко", "ано", "чуз"}
            words = [w for w in primary.split() if len(w) >= 3 and w not in orgforms]
            name_ok = bool(words and any(w in head for w in words))

            domain_ok = False
            if company.website_url:
                from urllib.parse import urlparse
                d = (urlparse(company.website_url).hostname or "").lower().lstrip("www.")
                if d:
                    domain_ok = d in head

            if not (name_ok or domain_ok):
                return AuditResult.reject(
                    "company_name_in_head",
                    f"neither company primary name nor website domain in first 300 chars (name='{primary}')",
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

    # Rule 6.5: corporate_email_only — холодный email шлём ТОЛЬКО на B2B-адреса.
    # Если адресат на gmail/mail.ru/yandex и т.п. — это личный ящик физлица,
    # рассылать туда без согласия = нарушение 152-ФЗ.
    # Исключение: если это reply в существующем conversation (человек сам нам
    # написал, conversation_id задан) — можно отвечать куда угодно.
    if message.channel == models.CHANNEL_EMAIL and not message.conversation_id:
        if is_personal_email(message.to_address):
            return AuditResult.reject(
                "corporate_email_only",
                f"cold outreach to personal email domain "
                f"({(message.to_address or '').split('@')[-1]}) is forbidden "
                "— use a corporate B2B address only",
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
