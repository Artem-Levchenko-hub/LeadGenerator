"""Пайплайн: HH → проверка сайта → Claude → SQLite."""
import logging
import random
import time
from datetime import datetime

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai.analyzer import analyze_company
from app.config import settings
from app.database import SessionLocal
from app.models import Lead, RunLog
from scrapers.hh_employers import HHClient, iter_employers_for_area
from scrapers.website_scraper import fetch_site

logger = logging.getLogger(__name__)


def should_skip_industry(industry_ids: list[str]) -> bool:
    """Если работодатель числится в IT-индустрии — пропускаем."""
    excluded = set(settings.excluded_industries_list)
    return any(iid in excluded for iid in industry_ids)


def company_already_in_db(db: Session, hh_id: str) -> bool:
    result = db.execute(
        select(Lead.id).where(Lead.hh_employer_id == hh_id).limit(1)
    ).first()
    return result is not None


def process_employer(
    db: Session,
    claude_client: anthropic.Anthropic,
    hh_client: HHClient,
    employer_stub: dict,
) -> Lead | None:
    hh_id = str(employer_stub["id"])
    if company_already_in_db(db, hh_id):
        return None

    employer = hh_client.get_employer(hh_id)
    if employer is None:
        return None

    if should_skip_industry(employer.top_level_industry_ids):
        logger.info("Skip IT employer %s %s", hh_id, employer.name)
        return None

    website_status = "unknown"
    website_url = None
    site_title = None
    site_description = None
    site_text = None
    site_stack: list[str] = []

    if employer.site_url:
        snap = fetch_site(employer.site_url)
        website_url = snap.final_url or snap.url
        if not snap.is_probably_alive:
            website_status = "dead_site"
        else:
            site_title = snap.title
            site_description = snap.description
            site_text = snap.text_excerpt
            site_stack = snap.detected_stack
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

    description_hh = None
    if employer.description_html:
        from bs4 import BeautifulSoup
        description_hh = BeautifulSoup(
            employer.description_html, "lxml"
        ).get_text(" ", strip=True)[:2500]

    industry_str = ", ".join(employer.industry_names) if employer.industry_names else None

    try:
        analysis = analyze_company(
            company_name=employer.name,
            industry=industry_str,
            city=employer.area_name,
            description_hh=description_hh,
            website_url=website_url,
            website_status=website_status,
            website_title=site_title,
            website_description=site_description,
            website_text=site_text,
            website_stack=site_stack,
            client=claude_client,
        )
    except Exception as e:
        logger.exception("Claude analysis failed for %s: %s", employer.name, e)
        return None

    lead = Lead(
        hh_employer_id=hh_id,
        company_name=employer.name,
        industry=industry_str,
        industry_ids=",".join(employer.industry_ids),
        description_hh=description_hh,
        city=employer.area_name,
        website_url=website_url,
        website_status=website_status,
        website_text_excerpt=site_text,
        ai_summary=analysis.summary,
        ai_pains=analysis.pains,
        ai_hook=analysis.sales_hook,
        ai_recommended_services=analysis.recommended_services,
        ai_priority=analysis.priority,
        ai_priority_reason=analysis.priority_reason,
        ai_analyzed_at=datetime.utcnow(),
        status="new",
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    logger.info("Saved lead: %s (priority=%s)", employer.name, analysis.priority)
    return lead


def run_pipeline_once(limit: int | None = None) -> dict:
    limit = limit or settings.leads_per_run
    run = RunLog(started_at=datetime.utcnow())

    db = SessionLocal()
    db.add(run)
    db.commit()

    fetched = 0
    created = 0
    errors = 0
    details: list[str] = []

    claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        cities = settings.cities_list[:]
        random.shuffle(cities)

        with HHClient() as hh:
            for area in cities:
                if created >= limit:
                    break
                for stub in iter_employers_for_area(hh, area=area, max_pages=10):
                    fetched += 1
                    if created >= limit:
                        break
                    try:
                        lead = process_employer(db, claude, hh, stub)
                        if lead is not None:
                            created += 1
                    except Exception as e:
                        errors += 1
                        details.append(f"{stub.get('name', '?')}: {e}")
                        logger.exception("Error processing %s", stub.get("name"))
                    time.sleep(0.2)

        run.success = True
    except Exception as e:
        run.success = False
        details.append(f"Pipeline failed: {e}")
        logger.exception("Pipeline failed")
    finally:
        run.finished_at = datetime.utcnow()
        run.companies_fetched = fetched
        run.leads_created = created
        run.errors = errors
        run.details = "\n".join(details[:50]) if details else None
        db.commit()
        db.close()

    return {"fetched": fetched, "created": created, "errors": errors}
