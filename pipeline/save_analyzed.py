"""Принимает JSON о компании → загружает MD-детали на Я.Диск + строку в xlsx.

Использование агентом Claude Code в /loop:
    echo '<json>' | py run.py save-analysis

Входной JSON:
{
  "company_name": "ООО Ромашка",
  "website_url": "https://romashka.ru",
  "phone": "+7 (495) 123-45-67",        // опционально
  "city": "Москва",
  "industry": "Юридические услуги",
  "website_status": "cms_site",
  "summary": "...",
  "pains": ["...", "..."],
  "recommended_services": ["..."],
  "sales_hook": "...",
  "priority": 4,
  "priority_reason": "..."
}
"""
import json
import logging
import re
import sys
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import ProcessedLead
from pipeline.yandex_sheet import (
    append_lead_row,
    upload_markdown_lead,
    upload_html_lead,
    _build_markdown,
    build_lead_page_html,
    regenerate_dashboard,
)

logger = logging.getLogger(__name__)


REQUIRED_FIELDS = [
    "company_name",
    "website_status",
    "summary",
    "pains",
    "recommended_services",
    "sales_hook",
    "priority",
    "priority_reason",
]


def _normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip().lower()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        p = urlparse(url)
        netloc = p.netloc.removeprefix("www.")
        return f"https://{netloc}{p.path}".rstrip("/")
    except Exception:
        return url


def _dedup_key(company_name: str, website_url: str | None) -> str:
    norm_url = _normalize_url(website_url)
    if norm_url:
        return f"url:{norm_url}"
    name = re.sub(r"[^\w\s]", "", company_name.lower())
    name = re.sub(r"\s+", " ", name).strip()
    return f"name:{name}"


def save_analysis(data: dict) -> dict:
    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        raise ValueError(f"Missing fields: {missing}")

    priority = int(data["priority"])
    if priority not in (1, 2, 3, 4, 5):
        raise ValueError(f"priority must be 1..5, got {priority}")

    pains = data["pains"]
    services = data["recommended_services"]
    if not isinstance(pains, list) or not pains:
        raise ValueError("'pains' must be non-empty list")
    if not isinstance(services, list) or not services:
        raise ValueError("'recommended_services' must be non-empty list")

    company_name = str(data["company_name"]).strip()
    website_url = data.get("website_url")
    phone = data.get("phone")
    city = data.get("city")
    industry = data.get("industry")
    website_status = data["website_status"]

    dedup_key = _dedup_key(company_name, website_url)

    init_db()
    db = SessionLocal()
    try:
        existing = db.scalar(
            select(ProcessedLead).where(ProcessedLead.dedup_key == dedup_key)
        )
        if existing is not None:
            print(
                f"[skip] duplicate: {company_name} "
                f"(row={existing.sheet_row}, priority={existing.priority})"
            )
            return {"skipped": True, "reason": "duplicate", "existing_id": existing.id}

        # 1) Собрать HTML-страницу лида (основная страница для дашборда)
        #    и MD-файл (для совместимости / бекапа).
        common_kwargs = dict(
            company_name=company_name,
            website_url=website_url,
            city=city,
            industry=industry,
            website_status=website_status,
            phone=phone,
            priority=priority,
            priority_reason=data["priority_reason"],
            summary=data["summary"],
            pains=[str(p) for p in pains],
            recommended_services=[str(s) for s in services],
            sales_hook=data["sales_hook"],
        )
        lead_html = build_lead_page_html(**common_kwargs)
        html_remote, html_url = upload_html_lead(company_name, lead_html)

        # MD — как бекап (не критично, не роняем save если упало)
        try:
            markdown = _build_markdown(**common_kwargs)
            upload_markdown_lead(company_name, markdown)
        except Exception as e:
            logger.warning("MD backup upload failed (non-fatal): %s", e)

        # В xlsx и дашборде ссылка "Детали" ведёт на HTML-страницу
        md_remote, md_url = html_remote, html_url

        # 2) Дописать компактную строку в xlsx
        row_num = append_lead_row(
            company_name=company_name,
            phone=phone,
            city=city,
            priority=priority,
            md_public_url=md_url,
            dedup_key=dedup_key,
        )

        # 3) Зафиксировать в SQLite
        processed = ProcessedLead(
            dedup_key=dedup_key,
            company_name=company_name,
            website_url=website_url,
            phone=phone,
            city=city,
            industry=industry,
            summary=data["summary"],
            recommended_services=[str(s) for s in services],
            priority=priority,
            sheet_row=row_num,
            md_public_url=md_url,
            analyzed_at=datetime.utcnow(),
        )
        db.add(processed)
        db.commit()
        db.refresh(processed)

        # 4) Перегенерировать мобильный HTML-дашборд
        all_leads = db.scalars(
            select(ProcessedLead).order_by(ProcessedLead.priority.desc())
        ).all()
        leads_data = [
            {
                "company_name": l.company_name,
                "phone": l.phone,
                "city": l.city,
                "industry": l.industry,
                "priority": l.priority,
                "summary": l.summary,
                "recommended_services": l.recommended_services or [],
                "website_url": l.website_url,
                "md_public_url": l.md_public_url,
                "analyzed_at": l.analyzed_at.isoformat() if l.analyzed_at else "",
            }
            for l in all_leads
        ]
        try:
            dashboard_url = regenerate_dashboard(leads_data)
        except Exception as e:
            logger.warning("Dashboard regen failed (non-fatal): %s", e)
            dashboard_url = None

        print(
            f"[ok] {company_name} → row {row_num}, priority={priority}, "
            f"md={md_remote}, id={processed.id}"
        )
        if dashboard_url:
            print(f"[dashboard] {dashboard_url}")
        return {
            "ok": True,
            "id": processed.id,
            "sheet_row": row_num,
            "priority": priority,
            "md_url": md_url,
            "dashboard_url": dashboard_url,
        }
    finally:
        db.close()


def main(argv: list[str]) -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print("Error: no JSON on stdin", file=sys.stderr)
        return 2
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON: {e}", file=sys.stderr)
        return 2
    try:
        result = save_analysis(data)
        return 0 if result.get("ok") or result.get("skipped") else 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main(sys.argv[1:]))
