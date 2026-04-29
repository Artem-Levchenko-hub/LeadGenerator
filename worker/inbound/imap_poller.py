"""IMAP-poller для входящих email-ответов.

Логика матчинга входящих сообщений к conversation:

1. Берём `In-Reply-To` и `References` headers из входящего письма.
2. Ищем `outbox_messages` с провайдерским Message-ID, совпадающим с
   In-Reply-To/References. Берём conversation_id оттуда.
3. Если совпадения нет — пытаемся матчить по `from_address` ↔ Company.contacts.email
   и создаём новый conversation.
4. Если и так нет совпадения — складируем в spam-bucket (просто помечаем как
   `unmatched`), не теряем.

При новом входящем создаётся `messages(direction='in')`,
обновляется `Conversation.last_inbound_at`,
ставится task `outreach.continue` (если ещё не стоит).
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import datetime
from email.header import decode_header
from email.utils import parseaddr

from app import models
from app.config import settings
from app.database import SessionLocal


log = logging.getLogger(__name__)


# Маркер последнего обработанного UID — чтобы не парсить INBOX каждый раз
# с самого начала. Хранится в простом файле (можно вынести в БД позже).
from pathlib import Path
_STATE_FILE = Path("data") / ".imap_last_uid"


def _load_last_uid() -> int:
    try:
        return int(_STATE_FILE.read_text().strip())
    except Exception:  # noqa: BLE001
        return 0


def _save_last_uid(uid: int) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(str(uid))
    except Exception:  # noqa: BLE001
        log.exception("failed to save imap last uid")


def _decode_str(s: str | bytes | None) -> str:
    if not s:
        return ""
    if isinstance(s, bytes):
        try:
            return s.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return s.decode("latin-1", errors="replace")
    parts = decode_header(s)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _extract_body(msg: email.message.Message) -> str:
    """Берёт plain-text часть. Если только html — грубо чистит теги."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace")
                except Exception:  # noqa: BLE001
                    return payload.decode("utf-8", errors="replace")
        # fallback на html
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                try:
                    raw = payload.decode(charset, errors="replace")
                except Exception:  # noqa: BLE001
                    raw = payload.decode("utf-8", errors="replace")
                return re.sub(r"<[^>]+>", " ", raw)
        return ""
    payload = msg.get_payload(decode=True) or b""
    charset = msg.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except Exception:  # noqa: BLE001
        return payload.decode("utf-8", errors="replace")


def _strip_quoted(text: str) -> str:
    """Грубо вырезает quoted-история из ответа (>... lines + 'On ... wrote')."""
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        ls = line.strip()
        if ls.startswith(">"):
            break
        if re.match(r"^On .* wrote:$", ls):
            break
        if re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}.*писал.*$", ls):
            break
        out.append(line)
    return "\n".join(out).strip()


def _normalize_message_id(mid: str | None) -> str | None:
    if not mid:
        return None
    return mid.strip().strip("<>").lower()


def _find_outbox_by_provider_id(db, provider_id: str | None) -> models.OutboxMessage | None:
    if not provider_id:
        return None
    return (
        db.query(models.OutboxMessage)
        .filter(models.OutboxMessage.provider_message_id == provider_id)
        .order_by(models.OutboxMessage.id.desc())
        .first()
    )


def _find_company_by_email(db, address: str) -> models.Company | None:
    if not address:
        return None
    address = address.lower().strip()
    # Ищем по contacts JSON (LIKE), потом — по доменной части
    candidates = (
        db.query(models.Company)
        .filter(models.Company.contacts.isnot(None))
        .all()
    )
    for c in candidates:
        contacts = c.contacts or {}
        emails = []
        if isinstance(contacts, dict):
            if "email" in contacts and contacts["email"]:
                emails.append(str(contacts["email"]).lower())
            for key, val in contacts.items():
                if "email" in key.lower() and val:
                    emails.append(str(val).lower())
        if address in emails:
            return c
    return None


def _process_message(db, raw: bytes) -> dict:
    msg = email.message_from_bytes(raw)
    from_name, from_addr = parseaddr(_decode_str(msg.get("From", "")))
    from_addr = (from_addr or "").lower()
    subject = _decode_str(msg.get("Subject", ""))
    in_reply_to = _normalize_message_id(msg.get("In-Reply-To"))
    references = msg.get("References", "") or ""
    ref_ids = [
        _normalize_message_id(r)
        for r in re.findall(r"<([^>]+)>", references)
    ]
    body_full = _extract_body(msg)
    body = _strip_quoted(body_full)[:8000]

    # 1) Match by In-Reply-To → outbox.provider_message_id → conversation
    candidates = [in_reply_to] + ref_ids
    outbox_match = None
    for cand in candidates:
        if not cand:
            continue
        outbox_match = _find_outbox_by_provider_id(db, cand)
        if outbox_match:
            break

    conv: models.Conversation | None = None
    company: models.Company | None = None

    if outbox_match and outbox_match.conversation_id:
        conv = db.query(models.Conversation).filter_by(
            id=outbox_match.conversation_id,
        ).one_or_none()
        if conv:
            company = db.query(models.Company).filter_by(
                id=conv.company_id,
            ).one_or_none()

    if not conv and outbox_match and outbox_match.company_id:
        # Был исходящий, но без conv — создаём conv ретроспективно
        company = db.query(models.Company).filter_by(
            id=outbox_match.company_id,
        ).one_or_none()
        if company:
            conv = models.Conversation(
                company_id=company.id,
                channel=models.CHANNEL_EMAIL,
                external_thread_id=outbox_match.provider_message_id,
                state=models.CONV_ENGAGED,
            )
            db.add(conv)
            db.commit()

    if not conv:
        # 2) match by from_address → existing Company
        company = _find_company_by_email(db, from_addr)
        if company:
            conv = (
                db.query(models.Conversation)
                .filter_by(company_id=company.id, channel=models.CHANNEL_EMAIL)
                .order_by(models.Conversation.id.desc())
                .first()
            )
            if not conv:
                conv = models.Conversation(
                    company_id=company.id,
                    channel=models.CHANNEL_EMAIL,
                    state=models.CONV_NEW,
                )
                db.add(conv)
                db.commit()

    if not conv:
        # 3) Полная неизвестность — пишем как unmatched в специальный лог
        # (просто log пока; в Спринте 3 — отдельная таблица unmatched_inbox).
        log.warning("Unmatched inbound from %s subject=%r", from_addr, subject[:80])
        return {"matched": False, "from": from_addr, "subject": subject[:80]}

    # 4) Создаём incoming message + обновляем conv
    in_msg = models.Message(
        conversation_id=conv.id,
        direction="in",
        body_text=body,
        sender=f"{from_name} <{from_addr}>" if from_name else from_addr,
        received_at=datetime.utcnow(),
        raw={
            "subject": subject[:200],
            "from": from_addr,
            "in_reply_to": in_reply_to,
            "references": ref_ids[:5],
        },
    )
    db.add(in_msg)
    conv.last_inbound_at = datetime.utcnow()
    if conv.state == models.CONV_NEW:
        conv.state = models.CONV_ENGAGED
    db.add(conv)

    # Если company была в stage=contacted/prospect → переходим в engaged
    if company and company.stage in (models.STAGE_PROSPECT, models.STAGE_CONTACTED):
        from_stage = company.stage
        company.stage = models.STAGE_ENGAGED
        company.last_stage_change_at = datetime.utcnow()
        db.add(company)
        db.add(models.StageHistory(
            company_id=company.id,
            from_stage=from_stage,
            to_stage=models.STAGE_ENGAGED,
            changed_by_agent="imap_poller",
            reason=f"reply received from {from_addr}",
        ))

    db.commit()

    # 5) Ставим задачу outreach.continue (если ещё не стоит)
    from worker.orchestrator import has_open_task, enqueue
    if not has_open_task(db, models.TASK_OUTREACH_CONT, company.id if company else 0):
        enqueue(
            db,
            kind=models.TASK_OUTREACH_CONT,
            company_id=company.id if company else None,
            conversation_id=conv.id,
            priority=8,
        )

    return {
        "matched": True,
        "company_id": company.id if company else None,
        "conversation_id": conv.id,
        "from": from_addr,
    }


def poll_inbox() -> dict:
    """Один проход поллера. Идемпотентно — использует UID-границу."""
    if not settings.imap_host or not settings.imap_user:
        return {"skipped": "imap not configured"}

    last_uid = _load_last_uid()
    new_messages = 0
    matched = 0
    unmatched = 0

    try:
        cli = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
        cli.login(settings.imap_user, settings.imap_password)
        cli.select("INBOX")
    except Exception as e:  # noqa: BLE001
        log.exception("IMAP connection failed")
        return {"error": f"connect: {type(e).__name__}: {e}"}

    try:
        criteria = f"UID {last_uid + 1}:*" if last_uid else "ALL"
        status, data = cli.uid("SEARCH", None, criteria)
        if status != "OK":
            return {"error": f"search: {status}"}
        uids = (data[0] or b"").split()
        if not uids:
            return {"new": 0, "last_uid": last_uid}

        max_uid = last_uid
        for uid in uids:
            uid_int = int(uid)
            if uid_int <= last_uid:
                continue
            try:
                _, fetched = cli.uid("FETCH", uid, "(RFC822)")
            except Exception:  # noqa: BLE001
                log.exception("FETCH failed for uid %s", uid)
                continue
            if not fetched or not fetched[0]:
                continue
            raw = fetched[0][1] if isinstance(fetched[0], tuple) else fetched[0]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            with SessionLocal() as db:
                try:
                    res = _process_message(db, bytes(raw))
                    if res.get("matched"):
                        matched += 1
                    else:
                        unmatched += 1
                    new_messages += 1
                except Exception:  # noqa: BLE001
                    log.exception("process_message failed for uid %s", uid_int)
            max_uid = max(max_uid, uid_int)

        if max_uid > last_uid:
            _save_last_uid(max_uid)
        return {
            "new": new_messages,
            "matched": matched,
            "unmatched": unmatched,
            "last_uid": max_uid,
        }
    finally:
        try:
            cli.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            cli.logout()
        except Exception:  # noqa: BLE001
            pass
