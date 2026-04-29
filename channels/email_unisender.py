"""Email канал через UniSender SMTP (или любой стандартный SMTP/TLS).

Отправляет одно сообщение синхронно, ставит Message-ID для последующего матчинга
входящих ответов с conversation.

inbound polling делается отдельным IMAP-poller'ом (см. worker/inbound/imap_poller.py
в Спринте 2).
"""
from __future__ import annotations

import logging
import smtplib
import uuid
from email.message import EmailMessage
from email.utils import make_msgid, formatdate

from app import models
from app.config import settings


log = logging.getLogger(__name__)


def _build_message(
    msg: models.OutboxMessage, message_id: str,
) -> EmailMessage:
    em = EmailMessage()
    em["From"] = f'"{settings.smtp_from_name}" <{settings.smtp_from_email}>'
    em["To"] = msg.to_address
    em["Subject"] = msg.subject or "Без темы"
    em["Date"] = formatdate(localtime=False)
    em["Message-ID"] = message_id

    # Headers для unsubscribe (RFC 2369 / 8058)
    unsubscribe_link = (
        f"https://lead-generator.ru/api/optout?token={msg.recall_token}"
        if msg.recall_token else None
    )
    if unsubscribe_link:
        em["List-Unsubscribe"] = f"<{unsubscribe_link}>"
        em["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    if msg.body_html:
        em.set_content(msg.body_text)
        em.add_alternative(msg.body_html, subtype="html")
    else:
        em.set_content(msg.body_text)

    return em


def send_email_sync(msg: models.OutboxMessage) -> dict:
    """Синхронная отправка через SMTP_SSL. Возвращает {ok, provider_id, error}."""
    if not settings.smtp_host or not settings.smtp_user:
        return {"ok": False, "error": "SMTP not configured (.env)"}

    domain = (settings.smtp_from_email or "stenvik.studio").split("@")[-1]
    message_id = make_msgid(domain=domain)

    try:
        em = _build_message(msg, message_id)
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(em)
    except Exception as e:  # noqa: BLE001
        log.exception("SMTP send failed for outbox %s", msg.id)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return {
        "ok": True,
        "provider": "unisender_smtp",
        "provider_id": message_id.strip("<>"),
    }
