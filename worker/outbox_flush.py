"""Outbox flush worker.

Каждую минуту:
1. Берёт draft-сообщения, прогоняет их через Auditor.
   - approved → status=holding (ждёт send_after).
   - rejected → status=rejected с причиной.
2. Берёт holding-сообщения, у которых send_after <= now → отправляет через
   соответствующий канал. status: sending → sent / failed.
3. Обновляет daily_quotas.sent_count.
"""
from __future__ import annotations

import logging
from datetime import datetime, date

from sqlalchemy import and_

from app import models
from app.database import SessionLocal
from worker.auditor import audit


log = logging.getLogger(__name__)


def _today_str() -> str:
    return date.today().isoformat()


def _bump_quota(db, channel: str) -> None:
    today = _today_str()
    q = db.query(models.DailyQuota).filter_by(date=today, channel=channel).one_or_none()
    if not q:
        # Лимит читается из settings, тут просто инициализируем.
        from app.config import settings
        limits = {
            models.CHANNEL_EMAIL:    settings.daily_email_limit,
            models.CHANNEL_TELEGRAM: settings.daily_telegram_limit,
            models.CHANNEL_SMS:      settings.daily_sms_limit,
            models.CHANNEL_CALL:     settings.daily_call_limit,
        }
        q = models.DailyQuota(
            date=today, channel=channel, sent_count=0,
            limit_count=limits.get(channel, 0),
        )
        db.add(q)
    q.sent_count = (q.sent_count or 0) + 1
    db.add(q)


def _send_via_channel(msg: models.OutboxMessage) -> dict:
    """Отправляет через нужный канал. Возвращает {ok: bool, provider_id?, error?}."""
    if msg.channel == models.CHANNEL_EMAIL:
        from channels.email_unisender import send_email_sync
        return send_email_sync(msg)
    # TODO Спринт 2: Telegram, SMS, Calls
    return {"ok": False, "error": f"channel '{msg.channel}' not implemented yet"}


def flush_drafts() -> dict:
    """Фаза 1: проверить все draft через Auditor."""
    approved = 0
    rejected = 0
    with SessionLocal() as db:
        drafts = (
            db.query(models.OutboxMessage)
            .filter(models.OutboxMessage.status == models.OUTBOX_DRAFT)
            .limit(50)
            .all()
        )
        for msg in drafts:
            r = audit(db, msg)
            if r.allowed:
                msg.status = models.OUTBOX_HOLDING
                msg.audit_notes = "approved"
                approved += 1
            else:
                msg.status = models.OUTBOX_REJECTED
                msg.audit_notes = f"[{r.rule}] {r.reason}"
                rejected += 1
            db.add(msg)
        db.commit()
    return {"approved": approved, "rejected": rejected}


def flush_due() -> dict:
    """Фаза 2: реально отправить holding-сообщения, у которых истёк холодильник."""
    sent = 0
    failed = 0
    with SessionLocal() as db:
        # kill_switch — выходим если paused
        ks = db.query(models.KillSwitch).filter_by(id=1).one_or_none()
        if ks and ks.state != "running":
            return {"skipped": f"kill_switch={ks.state}"}

        due = (
            db.query(models.OutboxMessage)
            .filter(and_(
                models.OutboxMessage.status == models.OUTBOX_HOLDING,
                models.OutboxMessage.send_after <= datetime.utcnow(),
            ))
            .limit(20)
            .all()
        )
        for msg in due:
            msg.status = models.OUTBOX_SENDING
            db.add(msg)
            db.commit()

            try:
                # Повторная проверка Auditor перед фактической отправкой
                # (за время холодильника могли появиться отписки/blacklist).
                r = audit(db, msg)
                if not r.allowed:
                    msg.status = models.OUTBOX_REJECTED
                    msg.audit_notes = (msg.audit_notes or "") + (
                        f" | revoked_at_send: [{r.rule}] {r.reason}"
                    )
                    db.add(msg)
                    db.commit()
                    failed += 1
                    continue

                result = _send_via_channel(msg)
                if result.get("ok"):
                    msg.status = models.OUTBOX_SENT
                    msg.sent_at = datetime.utcnow()
                    msg.provider_message_id = result.get("provider_id")
                    msg.provider = result.get("provider")
                    _bump_quota(db, msg.channel)
                    # Учёт исходящего в conversation
                    if msg.conversation_id:
                        conv = db.query(models.Conversation).filter_by(
                            id=msg.conversation_id,
                        ).one_or_none()
                        if conv:
                            conv.bot_messages_count = (conv.bot_messages_count or 0) + 1
                            conv.last_outbound_at = datetime.utcnow()
                            db.add(conv)
                    sent += 1
                else:
                    msg.status = models.OUTBOX_FAILED
                    msg.audit_notes = (msg.audit_notes or "") + (
                        f" | send_failed: {result.get('error', 'unknown')}"
                    )
                    failed += 1
                db.add(msg)
                db.commit()
            except Exception as e:  # noqa: BLE001
                log.exception("send failed for outbox %s", msg.id)
                msg.status = models.OUTBOX_FAILED
                msg.audit_notes = f"exception: {type(e).__name__}: {e}"
                db.add(msg)
                db.commit()
                failed += 1

    return {"sent": sent, "failed": failed}


def flush_all() -> dict:
    """Один пасс: сначала drafts, потом due."""
    return {
        "drafts": flush_drafts(),
        "due": flush_due(),
    }
