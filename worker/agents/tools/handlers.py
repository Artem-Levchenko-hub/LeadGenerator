"""Handlers — Python-реализации tools.

Каждая функция принимает kwargs из tool_input и возвращает строку
(то, что Anthropic API положит в tool_result для следующей итерации модели).

Все функции защищены от исключений — обёртка в run_react_loop ловит ошибки.
"""
from __future__ import annotations

import json
import re
import secrets
import socket
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app import models
from app.config import settings
from app.database import SessionLocal


# ============================================================
# fetch_site
# ============================================================

def fetch_site(*, url: str) -> str:
    """Скачивает сайт и возвращает структурированный анализ для LLM."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    timing = {}
    start = datetime.utcnow()
    try:
        with httpx.Client(
            follow_redirects=True, timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; StenvikBot/1.0)"},
        ) as client:
            resp = client.get(url)
        timing["load_seconds"] = round((datetime.utcnow() - start).total_seconds(), 2)
    except Exception as e:  # noqa: BLE001
        return json.dumps({
            "url": url,
            "status": "fetch_failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False)

    final_url = str(resp.url)
    is_https = final_url.startswith("https://")
    redirected_to_https = is_https and url.startswith("http://")

    soup = BeautifulSoup(resp.text or "", "lxml")
    title = (soup.title.string or "").strip() if soup.title else ""

    description_meta = soup.find("meta", attrs={"name": "description"})
    description = (description_meta.get("content") or "").strip() if description_meta else ""

    viewport_meta = soup.find("meta", attrs={"name": "viewport"})
    has_viewport = bool(viewport_meta)

    og_title = soup.find("meta", property="og:title")
    has_og = bool(og_title)

    text_content = soup.get_text(" ", strip=True)
    text_len = len(text_content)
    # 500 chars хватает чтобы понять отрасль/услуги; 1500 раздували tool_result
    # который дальше копится в messages всех итераций ReAct loop'а.
    text_sample = text_content[:500]

    # CMS-детект (грубый эвристический)
    html_low = (resp.text or "").lower()
    cms = _detect_cms(html_low, resp.headers)

    has_phone = bool(re.search(r"\+?7[\s\-()]*\d{3}", resp.text or ""))
    mailto_form = "mailto:" in html_low and "<form" in html_low
    has_form = "<form" in html_low

    # Контактные ссылки
    online_booking_hints = any(
        kw in html_low for kw in [
            "записаться", "онлайн-запис", "online-booking", "booking",
            "dikidi", "yclients",
        ]
    )

    # Контакты — emails и telegram-handle для выбора канала Outreach Agent'ом.
    contacts = _extract_contacts(resp.text or "", final_url)

    out = {
        "url": url,
        "final_url": final_url,
        "status_code": resp.status_code,
        "is_https": is_https,
        "redirected_to_https": redirected_to_https,
        "title": title[:150],
        "description": description[:200],
        "has_viewport_meta": has_viewport,
        "has_open_graph": has_og,
        "detected_cms": cms,
        "load_seconds": timing.get("load_seconds"),
        "has_phone_visible": has_phone,
        "has_form": has_form,
        "mailto_form_only": mailto_form and "@" in html_low.split("<form", 1)[-1][:2000],
        "online_booking_hints": online_booking_hints,
        "text_length": text_len,
        "text_sample": text_sample,
        "server_header": resp.headers.get("server", ""),
        "x_powered_by": resp.headers.get("x-powered-by", ""),
        "contacts": contacts,
    }
    return json.dumps(out, ensure_ascii=False)


_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)
_TG_LINK_RE = re.compile(r"(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_+]{3,})", re.I)
_TG_AT_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z0-9_]{4,})")
_VK_RE = re.compile(r"(?:https?://)?(?:m\.)?vk\.com/([A-Za-z0-9_.]{2,})", re.I)
_WA_RE = re.compile(
    r"(?:https?://)?(?:wa\.me|api\.whatsapp\.com/send)/?(?:\?phone=)?([+0-9]+)",
    re.I,
)


def _extract_contacts(html_text: str, page_url: str) -> dict:
    """Извлекает emails / telegram / vk / whatsapp с сайта компании.

    Email'ы делятся на 'corporate' (на этом же домене или другом B2B-домене) и
    'personal' (gmail/mail.ru/yandex/...). Outreach Agent выбирает channel
    исходя из доступных корпоративных адресов.
    """
    from worker.auditor import is_personal_email, PERSONAL_EMAIL_DOMAINS

    # Домен компании
    site_host = urlparse(page_url).hostname or ""
    site_host = site_host.lower().lstrip("www.")

    emails = set(_EMAIL_RE.findall(html_text))
    # Фильтруем технические/служебные (sentry, support@reportlab, etc. — обычно
    # не наши, но мы не знаем — пусть LLM решает)
    emails = {e for e in emails if not e.lower().endswith((".png", ".jpg", ".gif"))}

    corp_emails: list[str] = []
    own_domain_emails: list[str] = []
    personal_emails: list[str] = []
    for e in sorted(emails):
        domain = e.rsplit("@", 1)[1].lower()
        if is_personal_email(e):
            personal_emails.append(e)
        elif site_host and (domain == site_host or site_host.endswith("." + domain) or domain.endswith("." + site_host)):
            own_domain_emails.append(e)
            corp_emails.append(e)
        else:
            corp_emails.append(e)

    tg_handles = set()
    for m in _TG_LINK_RE.finditer(html_text):
        h = m.group(1)
        if h and h.lower() not in {"share", "iv", "joinchat"}:
            tg_handles.add(h)
    # @-mentions — берём осторожно (могут быть случайные слова в тексте);
    # сохраняем только если рядом упоминается слово telegram/телеграм.
    for m in _TG_AT_RE.finditer(html_text):
        ctx_start = max(0, m.start() - 60)
        ctx_end = min(len(html_text), m.end() + 60)
        ctx = html_text[ctx_start:ctx_end].lower()
        if "telegram" in ctx or "телеграм" in ctx or "t.me" in ctx:
            tg_handles.add(m.group(1))

    vk_handles = sorted({m.group(1) for m in _VK_RE.finditer(html_text)})
    whatsapp = sorted({m.group(1) for m in _WA_RE.finditer(html_text)})

    return {
        "site_host": site_host,
        "emails_corporate": corp_emails[:10],
        "emails_on_own_domain": own_domain_emails[:10],
        "emails_personal": personal_emails[:10],
        "telegram": sorted(tg_handles)[:10],
        "vk": vk_handles[:5],
        "whatsapp": whatsapp[:5],
        "any_b2b_email_found": bool(corp_emails),
    }


def _detect_cms(html_low: str, headers: dict) -> str:
    if "wp-content" in html_low or "wordpress" in html_low:
        return "wordpress"
    if "tilda" in html_low or "tildacdn" in html_low:
        return "tilda"
    if "wix.com" in html_low:
        return "wix"
    if "joomla" in html_low or "joomla" in (headers.get("x-powered-by") or "").lower():
        return "joomla"
    if "bitrix" in html_low or "/bitrix/" in html_low:
        return "1c-bitrix"
    if "drupal" in html_low:
        return "drupal"
    if "next.js" in html_low or "/_next/" in html_low:
        return "nextjs"
    return "unknown"


# ============================================================
# dns_check
# ============================================================

def dns_check(*, domain: str) -> str:
    """Проверяет DNS-записи: SPF, DMARC, MX. Возвращает JSON."""
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        return json.dumps({"error": "dnspython not installed"})

    domain = (domain or "").strip().lower()
    if not domain:
        return json.dumps({"error": "empty domain"})

    out: dict[str, Any] = {"domain": domain}
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0

    def _q(name: str, rrtype: str) -> list[str]:
        try:
            return [r.to_text() for r in resolver.resolve(name, rrtype)]
        except Exception:  # noqa: BLE001
            return []

    txts = _q(domain, "TXT")
    out["spf"] = next(
        (t for t in txts if "v=spf1" in t.lower()),
        None,
    )
    out["mx"] = _q(domain, "MX")[:5]
    dmarc_txts = _q(f"_dmarc.{domain}", "TXT")
    out["dmarc"] = next((t for t in dmarc_txts if "v=DMARC1" in t), None)
    out["has_spf"] = out["spf"] is not None
    out["has_dmarc"] = out["dmarc"] is not None
    out["has_mx"] = bool(out["mx"])
    out["dmarc_weak"] = bool(
        out["dmarc"] and ("p=none" in (out["dmarc"] or ""))
    )
    return json.dumps(out, ensure_ascii=False)


# ============================================================
# whois_lookup
# ============================================================

def whois_lookup(*, domain: str) -> str:
    try:
        import whois  # type: ignore
    except ImportError:
        return json.dumps({"error": "python-whois not installed"})

    try:
        info = whois.whois(domain)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"{type(e).__name__}: {e}"})

    creation = info.creation_date
    if isinstance(creation, list):
        creation = creation[0] if creation else None
    age_years: float | None = None
    if creation:
        try:
            age_years = round(
                (datetime.utcnow() - creation).days / 365.25, 1,
            )
        except Exception:  # noqa: BLE001
            pass
    return json.dumps({
        "domain": domain,
        "registrar": str(info.registrar or "")[:200],
        "creation_date": str(creation) if creation else None,
        "age_years": age_years,
    }, ensure_ascii=False, default=str)


# ============================================================
# record_weakness
# ============================================================

def record_weakness(
    *,
    company_id: int,
    kind: str,
    severity: str = "med",
    observation_text: str,
    evidence_url: str | None = None,
    suggested_fix: str | None = None,
    est_impact: str | None = None,
    est_effort: str | None = None,
) -> str:
    with SessionLocal() as db:
        w = models.LeadWeakness(
            company_id=company_id,
            kind=kind,
            severity=severity,
            observation_text=observation_text,
            evidence_url=evidence_url,
            suggested_fix=suggested_fix,
            est_impact=est_impact,
            est_effort=est_effort,
        )
        db.add(w)
        db.commit()
        return f"OK weakness_id={w.id}"


# ============================================================
# draft_message
# ============================================================

def draft_message(
    *,
    company_id: int,
    channel: str,
    to_address: str,
    body: str,
    subject: str | None = None,
    conversation_id: int | None = None,
    created_by_agent: str = "outreach",
) -> str:
    """Кладёт сообщение в outbox со статусом 'draft' и send_after = +holding.

    Сразу не одобряет — Auditor проверит при `outbox.flush_due()`.
    """
    holding = settings.outbox_holding_seconds
    send_after = datetime.utcnow() + timedelta(seconds=holding)
    recall = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        msg = models.OutboxMessage(
            company_id=company_id,
            conversation_id=conversation_id,
            channel=channel,
            to_address=to_address,
            subject=subject,
            body_text=body,
            status=models.OUTBOX_DRAFT,
            send_after=send_after,
            recall_token=recall,
            created_by_agent=created_by_agent,
        )
        db.add(msg)
        db.commit()
        return (
            f"OK outbox_id={msg.id} status=draft "
            f"send_after={send_after.isoformat()}Z recall_token={recall}"
        )


# ============================================================
# read_thread
# ============================================================

def read_thread(*, conversation_id: int) -> str:
    with SessionLocal() as db:
        msgs = (
            db.query(models.Message)
            .filter_by(conversation_id=conversation_id)
            .order_by(models.Message.id.asc())
            .all()
        )
        items = [{
            "direction": m.direction,
            "sender": m.sender,
            "body": m.body_text[:2000],
            "received_at": m.received_at.isoformat() if m.received_at else None,
        } for m in msgs]
    return json.dumps(items, ensure_ascii=False)


# ============================================================
# update_company / update_conversation_state
# ============================================================

ALLOWED_COMPANY_FIELDS = {
    "industry", "city", "country", "website_url", "contacts",
    "notes", "needs_human",
}


def update_company(*, company_id: int, fields: dict) -> str:
    if not isinstance(fields, dict):
        return "ERROR: fields must be an object"
    with SessionLocal() as db:
        c = db.query(models.Company).filter_by(id=company_id).one_or_none()
        if not c:
            return f"ERROR: company {company_id} not found"
        applied = []
        for k, v in fields.items():
            if k not in ALLOWED_COMPANY_FIELDS:
                continue
            setattr(c, k, v)
            applied.append(k)
        db.add(c)
        db.commit()
        return f"OK updated fields: {', '.join(applied) or 'none'}"


def update_conversation_state(
    *, conversation_id: int, state: str, reason: str | None = None,
) -> str:
    with SessionLocal() as db:
        conv = db.query(models.Conversation).filter_by(
            id=conversation_id,
        ).one_or_none()
        if not conv:
            return f"ERROR: conversation {conversation_id} not found"
        conv.state = state
        db.add(conv)
        # Если ready_for_proposal — отметим у компании stage=qualified
        if state == models.CONV_READY_FOR_PROPOSAL and conv.company_id:
            company = db.query(models.Company).filter_by(
                id=conv.company_id,
            ).one_or_none()
            if company and company.stage in (
                models.STAGE_ENGAGED, models.STAGE_QUALIFIED,
            ):
                company.stage = models.STAGE_QUALIFIED
                company.last_stage_change_at = datetime.utcnow()
                db.add(company)
        db.commit()
        return f"OK state={state}"


# ============================================================
# escalate_to_human
# ============================================================

def escalate_to_human(
    *, company_id: int, reason: str, conversation_id: int | None = None,
) -> str:
    with SessionLocal() as db:
        c = db.query(models.Company).filter_by(id=company_id).one_or_none()
        if c:
            c.needs_human = True
            c.notes = ((c.notes or "") + f"\n[escalated] {reason}").strip()
            db.add(c)
        if conversation_id:
            conv = db.query(models.Conversation).filter_by(
                id=conversation_id,
            ).one_or_none()
            if conv:
                conv.state = models.CONV_NEEDS_HUMAN
                db.add(conv)
        db.commit()
    return f"OK escalated company={company_id} reason={reason!r}"


# ============================================================
# finish
# ============================================================

def finish(*, summary: str) -> str:
    """Управляющий tool — обработка происходит в run_react_loop."""
    return summary


# ============================================================
# Регистрация всех handlers
# ============================================================

OUTREACH_HANDLERS = {
    "fetch_site": fetch_site,
    "dns_check": dns_check,
    "whois_lookup": whois_lookup,
    "record_weakness": record_weakness,
    "draft_message": draft_message,
    "read_thread": read_thread,
    "update_company": update_company,
    "update_conversation_state": update_conversation_state,
    "escalate_to_human": escalate_to_human,
    "finish": finish,
}
