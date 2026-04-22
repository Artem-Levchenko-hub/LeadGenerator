"""Ручной анализ одной компании — по URL сайта или по ID работодателя HH.

Использование:
    py run.py analyze https://example.com
    py run.py analyze "Название компании"   # ищем в HH
    py run.py analyze hh:1234567             # по HH employer_id
"""
import logging
import sys
from datetime import datetime

import anthropic
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select

from ai.analyzer import analyze_company
from app.config import settings
from app.database import SessionLocal, init_db
from app.models import Lead
from scrapers.hh_employers import HHClient
from scrapers.website_scraper import fetch_site

logger = logging.getLogger(__name__)


def _guess_company_name_from_site(snap) -> str:
    if snap.title:
        title = snap.title
        for sep in [" | ", " — ", " - ", " · "]:
            if sep in title:
                parts = [p.strip() for p in title.split(sep)]
                parts.sort(key=len)
                title = parts[0]
                break
        return title[:200]
    return snap.url


def analyze_by_url(url: str, save: bool = True) -> dict:
    print(f"\n🌐 Fetching {url} ...")
    snap = fetch_site(url)

    if not snap.is_probably_alive:
        print(f"⚠️  Сайт не открылся: {snap.error or snap.status_code}")
        website_status = "dead_site"
    else:
        stack_joined = " ".join(snap.detected_stack).lower()
        if "tilda" in stack_joined or "wix" in stack_joined:
            website_status = "constructor_site"
        elif any(cms in stack_joined for cms in ["wordpress", "joomla", "bitrix", "drupal"]):
            website_status = "cms_site"
        elif not snap.has_https:
            website_status = "no_https"
        else:
            website_status = "has_site"
        print(f"✅ Статус: {website_status}, стек: {snap.detected_stack or '—'}")

    company_name = _guess_company_name_from_site(snap)
    print(f"🏢 Имя (угадано): {company_name}")

    print("\n🤖 Анализирую через Claude Sonnet 4.6 ...")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    analysis = analyze_company(
        company_name=company_name,
        industry=None,
        city=None,
        description_hh=None,
        website_url=snap.final_url or snap.url,
        website_status=website_status,
        website_title=snap.title,
        website_description=snap.description,
        website_text=snap.text_excerpt,
        website_stack=snap.detected_stack,
        client=client,
    )

    print("\n" + "=" * 70)
    print(f"📊 ПРИОРИТЕТ: {'⭐' * analysis.priority}  ({analysis.priority}/5)")
    print(f"   Причина: {analysis.priority_reason}")
    print("\n💼 КРАТКО:")
    print(f"   {analysis.summary}")
    print("\n🔥 БОЛИ:")
    for i, pain in enumerate(analysis.pains, 1):
        print(f"   {i}. {pain}")
    print("\n🛠️  РЕКОМЕНДОВАННЫЕ УСЛУГИ STENVIK:")
    for s in analysis.recommended_services:
        print(f"   • {s}")
    print("\n💬 ХУК ДЛЯ ПРОДАЖНИКА:")
    print(f"   {analysis.sales_hook}")
    print("=" * 70)

    if save:
        db = SessionLocal()
        try:
            hh_id = f"manual:{url}"
            existing = db.scalar(select(Lead).where(Lead.hh_employer_id == hh_id))
            if existing:
                print(f"\n(Обновил существующий лид #{existing.id})")
                lead = existing
            else:
                lead = Lead(hh_employer_id=hh_id, status="new")
                db.add(lead)
            lead.company_name = company_name
            lead.website_url = snap.final_url or snap.url
            lead.website_status = website_status
            lead.website_text_excerpt = snap.text_excerpt
            lead.ai_summary = analysis.summary
            lead.ai_pains = analysis.pains
            lead.ai_hook = analysis.sales_hook
            lead.ai_recommended_services = analysis.recommended_services
            lead.ai_priority = analysis.priority
            lead.ai_priority_reason = analysis.priority_reason
            lead.ai_analyzed_at = datetime.utcnow()
            db.commit()
            db.refresh(lead)
            print(f"💾 Сохранено в БД: лид #{lead.id} — /leads/{lead.id}")
        finally:
            db.close()

    return analysis.model_dump()


def analyze_by_hh_id(hh_id: str, save: bool = True) -> dict:
    print(f"\n📋 Fetching HH employer {hh_id} ...")
    with HHClient() as hh:
        employer = hh.get_employer(hh_id)
    if employer is None:
        print(f"❌ Работодатель {hh_id} не найден")
        return {}

    description_hh = None
    if employer.description_html:
        description_hh = BeautifulSoup(
            employer.description_html, "lxml"
        ).get_text(" ", strip=True)[:2500]

    website_status = "unknown"
    snap = None
    if employer.site_url:
        print(f"🌐 Fetching site {employer.site_url} ...")
        snap = fetch_site(employer.site_url)
        if not snap.is_probably_alive:
            website_status = "dead_site"
        else:
            stack_joined = " ".join(snap.detected_stack).lower()
            if "tilda" in stack_joined or "wix" in stack_joined:
                website_status = "constructor_site"
            elif any(cms in stack_joined for cms in ["wordpress", "joomla", "bitrix", "drupal"]):
                website_status = "cms_site"
            elif not snap.has_https:
                website_status = "no_https"
            else:
                website_status = "has_site"
    else:
        website_status = "no_site"
        print("⚠️  У компании нет сайта в профиле HH")

    print(f"🏢 {employer.name} · {employer.area_name or '—'} · {', '.join(employer.industry_names) or '—'}")

    print("\n🤖 Анализирую через Claude Sonnet 4.6 ...")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    analysis = analyze_company(
        company_name=employer.name,
        industry=", ".join(employer.industry_names) or None,
        city=employer.area_name,
        description_hh=description_hh,
        website_url=(snap.final_url or snap.url) if snap else None,
        website_status=website_status,
        website_title=snap.title if snap else None,
        website_description=snap.description if snap else None,
        website_text=snap.text_excerpt if snap else None,
        website_stack=snap.detected_stack if snap else None,
        client=client,
    )

    print("\n" + "=" * 70)
    print(f"📊 ПРИОРИТЕТ: {'⭐' * analysis.priority}  ({analysis.priority}/5)")
    print(f"   {analysis.priority_reason}")
    print(f"\n💼 {analysis.summary}")
    print("\n🔥 БОЛИ:")
    for i, p in enumerate(analysis.pains, 1):
        print(f"   {i}. {p}")
    print("\n🛠️  УСЛУГИ: " + ", ".join(analysis.recommended_services))
    print("\n💬 ХУК:")
    print(f"   {analysis.sales_hook}")
    print("=" * 70)

    if save:
        db = SessionLocal()
        try:
            existing = db.scalar(select(Lead).where(Lead.hh_employer_id == str(employer.id)))
            lead = existing or Lead(hh_employer_id=str(employer.id), status="new")
            if not existing:
                db.add(lead)
            lead.company_name = employer.name
            lead.industry = ", ".join(employer.industry_names) or None
            lead.industry_ids = ",".join(employer.industry_ids)
            lead.description_hh = description_hh
            lead.city = employer.area_name
            lead.website_url = (snap.final_url or snap.url) if snap else None
            lead.website_status = website_status
            lead.website_text_excerpt = snap.text_excerpt if snap else None
            lead.ai_summary = analysis.summary
            lead.ai_pains = analysis.pains
            lead.ai_hook = analysis.sales_hook
            lead.ai_recommended_services = analysis.recommended_services
            lead.ai_priority = analysis.priority
            lead.ai_priority_reason = analysis.priority_reason
            lead.ai_analyzed_at = datetime.utcnow()
            db.commit()
            db.refresh(lead)
            print(f"💾 Сохранено в БД: лид #{lead.id}")
        finally:
            db.close()

    return analysis.model_dump()


def main(argument: str):
    init_db()

    if argument.startswith("hh:"):
        analyze_by_hh_id(argument[3:])
    elif argument.startswith(("http://", "https://")) or "." in argument.split("/")[0]:
        analyze_by_url(argument)
    else:
        # company name → search in HH
        print(f"🔍 Ищу '{argument}' на HH.ru ...")
        with HHClient() as hh:
            data = hh.list_employers(text=argument, per_page=5)
        items = data.get("items", [])
        if not items:
            print("❌ Ничего не нашлось")
            return
        print(f"Нашёл {len(items)} вариантов:")
        for i, item in enumerate(items, 1):
            print(f"  {i}. {item.get('name')} (id={item['id']})")
        if len(items) == 1:
            analyze_by_hh_id(str(items[0]["id"]))
        else:
            choice = input("\nВыбери номер (или Enter для первого): ").strip()
            idx = int(choice) - 1 if choice else 0
            analyze_by_hh_id(str(items[idx]["id"]))


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
