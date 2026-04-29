"""2GIS Catalog API — главный источник холодных B2B-лидов.

Бесплатный тариф API возвращает: name, address, adm_div (город/район),
rubrics, attribute_groups (теги услуг). Контакты (сайт/email/телефон) —
платно через API, но публичная страница 2gis.ru/firm/{id} рендерит их в
встроенный SSR-JSON. После search-запроса мы обогащаем каждый item через
worker.hunter.enrichment.enrich_firm — это даёт реальный сайт компании
для Outreach Agent.

Стратегия:
- Перебираем (категория × город) парами из settings — но **детерминированно
  ротируем** стартовую пару и страницу через персистентный счётчик тиков
  в data/hunter_twogis_state.json. Без этого Hunter залипал на (pairs[0],
  page=1) и выдавал одни и те же 10 фирм каждый тик.
- Формула: start_pair = tick % len(pairs), page = (tick // len(pairs)) % MAX_PAGE + 1.
  Это даёт len(pairs) × MAX_PAGE уникальных стартовых точек до повторения
  (~3-7 дней непрерывной работы Hunter'а с шагом 20 мин).
- 2GIS Free-tier: page_size > 10 даёт 400. Берём 10.
- Для каждого попавшегося item — обогащаем (сайт/телефон/email).
- Дедуп по 2gis item.id.

Доки: https://docs.2gis.com/ru/api/search/places/reference/3.0/items
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Iterable, Iterator

import httpx

from app.config import settings
from worker.hunter.enrichment import enrich_firm
from worker.hunter.sources.base import LeadHit, LeadSource


log = logging.getLogger(__name__)


_API_URL = "https://catalog.api.2gis.com/3.0/items"
_FIELDS = (
    "items.point,items.address,items.adm_div,items.org,"
    "items.id,items.full_name,items.rubrics,items.attribute_groups"
)
_STATE_FILE = Path("data/hunter_twogis_state.json")
_MAX_PAGE = 5  # 2GIS free-tier обычно отдаёт 5 страниц по 10 item.


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:  # noqa: BLE001
        log.exception("failed to write hunter state")


class TwoGISSource(LeadSource):
    name = "2gis"

    def __init__(
        self,
        api_key: str | None = None,
        cities: list[str] | None = None,
        categories: list[str] | None = None,
        enrich: bool = True,
        enrich_sleep_seconds: float = 0.5,
    ):
        self.api_key = api_key or settings.twogis_api_key
        self.cities = cities or settings.twogis_cities_list
        self.categories = categories or settings.twogis_categories_list
        self.enrich = enrich
        self.enrich_sleep_seconds = enrich_sleep_seconds

    def iter_leads(self, *, limit: int = 50) -> Iterator[LeadHit]:
        if not self.api_key:
            log.warning("2GIS api key not set, skipping")
            return

        emitted = 0
        pairs = [(cat, city) for cat in self.categories for city in self.cities]
        if not pairs:
            return

        # Детерминированная ротация: каждый тик стартуем с другой пары и
        # страницы. Без этого мы залипали на pairs[0] page=1 → одни и те
        # же 10 фирм в каждом тике → 0 новых.
        state = _load_state()
        tick = int(state.get("tick", 0)) + 1
        state["tick"] = tick
        _save_state(state)

        n = len(pairs)
        start = tick % n
        page = (tick // n) % _MAX_PAGE + 1
        rotated = pairs[start:] + pairs[:start]
        log.info(
            "2gis hunter: tick=%d start_pair=%s page=%d (pairs=%d, max_page=%d)",
            tick, rotated[0], page, n, _MAX_PAGE,
        )

        for category, city in rotated:
            if emitted >= limit:
                return
            try:
                # 2GIS Free-tier: page_size > 10 даёт 400. Берём 10.
                items = self._search_page(category, city, page_size=10, page=page)
            except Exception:  # noqa: BLE001
                log.exception("2gis search failed for %s/%s page=%d", category, city, page)
                continue
            for item in items:
                if emitted >= limit:
                    return
                hit = self._item_to_hit(item, category, city)
                if hit:
                    if self.enrich:
                        self._enrich_in_place(hit)
                        if self.enrich_sleep_seconds > 0:
                            time.sleep(self.enrich_sleep_seconds)
                    yield hit
                    emitted += 1

    def _enrich_in_place(self, hit: LeadHit) -> None:
        """Дотягиваем сайт/телефон/email из публичной 2gis.ru/firm/{id}."""
        if not hit.source_id:
            return
        contacts = enrich_firm(hit.source_id)
        if not contacts:
            return
        if contacts.website and not hit.website_url:
            hit.website_url = contacts.website
        if contacts.phone and not hit.phone:
            hit.phone = contacts.phone
        if contacts.email and not hit.email:
            hit.email = contacts.email

    def _search_page(
        self, category: str, city: str, page_size: int, page: int = 1,
    ) -> list[dict]:
        params = {
            "q": f"{category} {city}",
            "page_size": str(page_size),
            "page": str(page),
            "fields": _FIELDS,
            "key": self.api_key,
        }
        with httpx.Client(timeout=20.0) as client:
            r = client.get(_API_URL, params=params)
        if r.status_code != 200:
            log.warning("2gis %s %s: status=%s body=%s",
                        category, city, r.status_code, r.text[:200])
            return []
        data = r.json()
        if data.get("meta", {}).get("code") not in (200, None):
            return []
        return data.get("result", {}).get("items", []) or []

    @staticmethod
    def _item_to_hit(item: dict, category: str, city_query: str) -> LeadHit | None:
        name = (item.get("name") or item.get("full_name") or "").strip()
        if not name:
            return None
        item_id = item.get("id")
        org = item.get("org") or {}
        org_name = org.get("name") if isinstance(org, dict) else None

        address = item.get("address_name") or ""
        adm = item.get("adm_div") or []
        city_name = next(
            (a.get("name") for a in adm if a.get("type") == "city"),
            None,
        ) or city_query

        # 2GIS card URL — используется Outreach Agent'ом для поиска сайта.
        source_url = (
            f"https://2gis.ru/firm/{item_id}" if item_id else None
        )

        return LeadHit(
            name=org_name or name,
            source="2gis",
            source_id=str(item_id) if item_id else None,
            source_url=source_url,
            city=city_name,
            industry=category,
            address=address,
            raw={
                "rubrics": [r.get("name") for r in item.get("rubrics", [])][:5],
                "attributes": [
                    a.get("name")
                    for ag in item.get("attribute_groups", [])
                    for a in (ag.get("attributes") or [])
                ][:8],
            },
        )
