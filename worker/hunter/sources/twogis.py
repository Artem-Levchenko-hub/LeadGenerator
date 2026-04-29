"""2GIS Catalog API — главный источник холодных B2B-лидов.

Бесплатный тариф API возвращает: name, address, adm_div (город/район),
rubrics, attribute_groups (теги услуг). Контакты (сайт/email/телефон) —
платно, поэтому сайт мы будем потом искать через Outreach Agent fetch_site
по адресу 2GIS-карточки.

Стратегия:
- Перебираем (категория × город) парами из settings.
- Берём первую страницу page_size=20 на каждую пару (хватит на 1 тик).
- Дедуп по 2gis item.id.
- Не сохраняем компании на которые уже есть Company.lead_id связь.

Доки: https://docs.2gis.com/ru/api/search/places/reference/3.0/items
"""
from __future__ import annotations

import logging
from typing import Iterable, Iterator

import httpx

from app.config import settings
from worker.hunter.sources.base import LeadHit, LeadSource


log = logging.getLogger(__name__)


_API_URL = "https://catalog.api.2gis.com/3.0/items"
_FIELDS = (
    "items.point,items.address,items.adm_div,items.org,"
    "items.id,items.full_name,items.rubrics,items.attribute_groups"
)


class TwoGISSource(LeadSource):
    name = "2gis"

    def __init__(
        self,
        api_key: str | None = None,
        cities: list[str] | None = None,
        categories: list[str] | None = None,
    ):
        self.api_key = api_key or settings.twogis_api_key
        self.cities = cities or settings.twogis_cities_list
        self.categories = categories or settings.twogis_categories_list

    def iter_leads(self, *, limit: int = 20) -> Iterator[LeadHit]:
        if not self.api_key:
            log.warning("2GIS api key not set, skipping")
            return

        emitted = 0
        for category in self.categories:
            for city in self.cities:
                if emitted >= limit:
                    return
                try:
                    items = self._search_page(category, city, page_size=10)
                except Exception:  # noqa: BLE001
                    log.exception("2gis search failed for %s/%s", category, city)
                    continue
                for item in items:
                    if emitted >= limit:
                        return
                    hit = self._item_to_hit(item, category, city)
                    if hit:
                        yield hit
                        emitted += 1

    def _search_page(self, category: str, city: str, page_size: int) -> list[dict]:
        params = {
            "q": f"{category} {city}",
            "page_size": str(page_size),
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
