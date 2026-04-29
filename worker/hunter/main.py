"""Hunter runner — основной cycle.

Запускается из worker/main.py каждые 30 минут:
1. Перебирает все активные `LeadSource`.
2. Для каждого LeadHit делает дедуп по нормализованному ключу против Company.
3. Создаёт Company(stage=prospect) + StageHistory(from=None, to=prospect).
4. Пишет лог запуска в RunLog.

В первой версии — только 2GIS. HH через /loop остаётся отдельным каналом.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

from sqlalchemy import or_

from app import models
from app.database import SessionLocal
from worker.hunter.sources.base import LeadHit, LeadSource
from worker.hunter.sources.twogis import TwoGISSource
from worker.hunter.sources.hh import HHEmployersSource


log = logging.getLogger(__name__)


def _normalize_name(s: str) -> str:
    return (s or "").strip().lower()


def _company_exists(db, hit: LeadHit) -> bool:
    name_norm = _normalize_name(hit.name)
    city_norm = _normalize_name(hit.city or "")

    # 1) Точное совпадение по 2gis source_id (если есть) — самый строгий
    if hit.source_id:
        # Храним source_id в contacts JSON
        existing = (
            db.query(models.Company)
            .filter(models.Company.contacts.isnot(None))
            .all()
        )
        for c in existing:
            if isinstance(c.contacts, dict) and c.contacts.get(f"{hit.source}_id") == hit.source_id:
                return True

    # 2) По name + city
    candidates = (
        db.query(models.Company)
        .filter(models.Company.name.ilike(hit.name))
        .all()
    )
    for c in candidates:
        if _normalize_name(c.name) == name_norm and _normalize_name(c.city or "") == city_norm:
            return True

    # 3) Также против processed_leads — старые лиды от /loop
    pl = (
        db.query(models.ProcessedLead)
        .filter(models.ProcessedLead.company_name.ilike(hit.name))
        .first()
    )
    if pl:
        return True

    return False


def _save_hit(db, hit: LeadHit) -> models.Company | None:
    if _company_exists(db, hit):
        return None

    contacts: dict = {}
    if hit.source_id:
        contacts[f"{hit.source}_id"] = hit.source_id
    if hit.source_url:
        contacts[f"{hit.source}_url"] = hit.source_url
    if hit.address:
        contacts["address"] = hit.address
    if hit.phone:
        contacts["phone"] = hit.phone
    if hit.email:
        contacts["email"] = hit.email
    if hit.raw:
        contacts["meta"] = hit.raw

    c = models.Company(
        name=hit.name[:512],
        website_url=hit.website_url,
        industry=(hit.industry or "")[:255] or None,
        city=(hit.city or "")[:127] or None,
        country="RU",
        contacts=contacts,
        stage=models.STAGE_PROSPECT,
        last_stage_change_at=datetime.utcnow(),
    )
    db.add(c)
    db.flush()  # получаем c.id

    db.add(models.StageHistory(
        company_id=c.id,
        from_stage=None,
        to_stage=models.STAGE_PROSPECT,
        changed_by_agent=f"hunter.{hit.source}",
        reason=f"first ingestion from {hit.source}: {hit.industry or ''} / {hit.city or ''}",
    ))
    db.commit()
    return c


def get_active_sources() -> list[LeadSource]:
    sources: list[LeadSource] = []
    s_2gis = TwoGISSource()
    if s_2gis.api_key and s_2gis.cities and s_2gis.categories:
        sources.append(s_2gis)
    # HH employers — бесплатный нативный API, всегда активен.
    sources.append(HHEmployersSource())
    return sources


def run_one_tick(*, max_per_tick: int = 5) -> dict:
    """Один проход Hunter'а. Возвращает отчёт."""
    sources = get_active_sources()
    if not sources:
        return {"skipped": "no active sources (check TWOGIS_API_KEY etc.)"}

    started = datetime.utcnow()
    created = 0
    skipped_dup = 0
    seen = 0
    errors = 0

    with SessionLocal() as db:
        run = models.RunLog(started_at=started, success=False)
        db.add(run)
        db.commit()
        run_id = run.id

    for source in sources:
        try:
            for hit in source.iter_leads(limit=max_per_tick - created):
                seen += 1
                with SessionLocal() as db:
                    try:
                        saved = _save_hit(db, hit)
                        if saved is None:
                            skipped_dup += 1
                        else:
                            created += 1
                    except Exception:  # noqa: BLE001
                        log.exception("save_hit failed for %r", hit.name)
                        errors += 1
                if created >= max_per_tick:
                    break
        except Exception:  # noqa: BLE001
            log.exception("source %s failed", source.name)
            errors += 1
        if created >= max_per_tick:
            break

    with SessionLocal() as db:
        run = db.query(models.RunLog).filter_by(id=run_id).one_or_none()
        if run:
            run.finished_at = datetime.utcnow()
            run.leads_created = created
            run.errors = errors
            run.success = errors == 0
            run.details = (
                f"hunter: sources={[s.name for s in sources]} "
                f"seen={seen} created={created} dup={skipped_dup} errors={errors}"
            )
            db.add(run)
            db.commit()

    return {
        "sources": [s.name for s in sources],
        "seen": seen,
        "created": created,
        "skipped_dup": skipped_dup,
        "errors": errors,
    }
