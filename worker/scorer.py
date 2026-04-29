"""Scorer — приоритизирует Companies БЕЗ LLM.

Цель: не тратить токены на cold leads. Scorer пробегает по Company,
делает ОДИН fetch_site (httpx + BeautifulSoup, ноль LLM), считает
эвристический score 0..100. Orchestrator потом enqueue'ит Outreach
только на лиды у которых score >= threshold.

Score components:
- ICP-индустрия (стом/клиника/автосервис/...)  +15
- Крупный город                                 +5
- Нет сайта вообще (надо лендинг)               +35
- Сайт недоступен (404/timeout)                 +25
- CMS Tilda/Wix (без интеграций)                +15
- CMS Bitrix/Joomla (устарело 5+ лет)           +25
- Нет HTTPS                                     +25
- Нет mobile viewport                           +15
- Тонкий контент (<500 chars)                   +15
- Только mailto-форма                           +10
- Сервисный бизнес без онлайн-записи            +25
- Корп-email уже найден (можно слать)           +5

Итого max ~150, обрезаем до 100.

Запуск:
    .venv/Scripts/python.exe -m worker.scorer score-all
    .venv/Scripts/python.exe -m worker.scorer score 42      # company_id=42
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from typing import Iterable

from app import models
from app.database import SessionLocal


log = logging.getLogger(__name__)


_ICP_INDUSTRIES = {
    "стоматолог", "клиник", "медицин", "автосервис", "автомастер",
    "юрист", "адвокат", "бухгалтер", "консалт",
    "красот", "салон", "парикмах", "стри",
    "стройк", "ремонт", "недвижим",
    "образован", "репетитор", "школ",
    "ресторан", "кафе", "горест", "горка",
}

_BIG_CITIES = {
    "москва", "санкт-петербург", "санктпетер", "спб",
    "краснодар", "казань", "екатеринбург",
    "новосибирск", "ростов", "самара", "уфа", "челябинск", "нижний",
}

_SERVICE_INDUSTRIES_NEEDING_BOOKING = {
    "стоматолог", "клиник", "медицин",
    "красот", "салон", "парикмах",
    "автосервис", "автомастер",
}


def _matches_any(text: str, keywords: Iterable[str]) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in keywords)


def score_company(company: models.Company) -> tuple[int, str]:
    """Считает score 0..100 и возвращает (score, reason).

    reason — короткая строка с тегами через `;`, объясняющая что повлияло.
    """
    score = 0
    reasons: list[str] = []

    # ICP-индустрия
    if _matches_any(company.industry or "", _ICP_INDUSTRIES):
        score += 15
        reasons.append("icp")
    else:
        reasons.append("not_icp")

    # Крупный город
    if _matches_any(company.city or "", _BIG_CITIES):
        score += 5
        reasons.append("big_city")

    # Нет сайта — главный сигнал что нужен лендинг/корпсайт
    if not company.website_url:
        score += 35
        reasons.append("no_website")
        return min(100, score), ";".join(reasons)

    # Есть сайт — fetch и анализируем (без LLM, чистый Python)
    try:
        from worker.agents.tools.handlers import fetch_site as _fetch
        result = json.loads(_fetch(url=company.website_url))
    except Exception as e:  # noqa: BLE001
        log.warning("scorer fetch failed for %s: %s", company.website_url, e)
        score += 25
        reasons.append("fetch_error")
        return min(100, score), ";".join(reasons)

    if result.get("status") == "fetch_failed":
        score += 25
        reasons.append("site_unreachable")
        return min(100, score), ";".join(reasons)

    cms = (result.get("detected_cms") or "").lower()
    if cms in ("tilda", "wix"):
        score += 15
        reasons.append(f"cms_{cms}")
    elif cms in ("1c-bitrix", "joomla"):
        score += 25
        reasons.append(f"cms_outdated_{cms}")

    if not result.get("is_https"):
        score += 25
        reasons.append("no_https")
    if not result.get("has_viewport_meta"):
        score += 15
        reasons.append("no_mobile")
    if (result.get("text_length") or 0) < 500:
        score += 15
        reasons.append("thin_content")
    if result.get("mailto_form_only"):
        score += 10
        reasons.append("mailto_only")

    # Сервисный бизнес без онлайн-записи — горячий
    if _matches_any(company.industry or "", _SERVICE_INDUSTRIES_NEEDING_BOOKING):
        if not result.get("online_booking_hints"):
            score += 25
            reasons.append("service_no_booking")

    if (result.get("contacts") or {}).get("any_b2b_email_found"):
        score += 5
        reasons.append("has_b2b_email")

    return min(100, score), ";".join(reasons)


def score_one(company_id: int) -> dict:
    """Скорит одну компанию и сохраняет в БД."""
    with SessionLocal() as db:
        c = db.query(models.Company).filter_by(id=company_id).one_or_none()
        if not c:
            return {"error": f"company {company_id} not found"}
        score, reason = score_company(c)
        c.score = score
        c.score_reason = reason[:1000]
        c.score_updated_at = datetime.utcnow()
        db.add(c)
        db.commit()
        return {
            "company_id": c.id,
            "name": c.name[:60],
            "score": score,
            "reason": reason,
        }


def score_all_unscored(limit: int = 50) -> dict:
    """Скорит ВСЕ Company которые ещё без score (или давно не обновлялись)."""
    with SessionLocal() as db:
        rows = (
            db.query(models.Company)
            .filter(models.Company.score.is_(None))
            .order_by(models.Company.id.desc())
            .limit(limit)
            .all()
        )
        ids = [r.id for r in rows]
    results = []
    for cid in ids:
        try:
            results.append(score_one(cid))
        except Exception as e:  # noqa: BLE001
            log.exception("score_one failed for company %d", cid)
            results.append({"company_id": cid, "error": str(e)})
    return {
        "scored": len(results),
        "results": results,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = sys.argv[1:]
    if not args:
        print("usage: python -m worker.scorer (score <id> | score-all)")
        return 1
    cmd = args[0]
    if cmd == "score" and len(args) >= 2:
        result = score_one(int(args[1]))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if cmd == "score-all":
        result = score_all_unscored(limit=200)
        for r in result["results"]:
            score = r.get("score", "ERR")
            name = r.get("name", "?")
            reason = r.get("reason", r.get("error", ""))
            print(f"  {score:>3}  #{r.get('company_id'):>3}  {name[:50]:50}  {reason[:80]}")
        print(f"\nScored {result['scored']} companies")
        return 0
    print(f"unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
