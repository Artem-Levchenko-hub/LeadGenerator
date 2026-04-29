"""HH.ru Employers — бесплатный нативный API без ключа.

Документация: https://github.com/hhru/api/blob/master/docs/employers.md
- GET /employers?text=...&area=...&per_page=100&page=N — поиск работодателей.
- GET /employers/{id} — детальная карточка с site_url и т.д.

Лимит вежливости HH: ~5–10 запросов/сек. Через 2-3 страницы на категорию×город
получаем десятки уникальных компаний. С нашей частотой (раз в 30 мин) — сотни
запросов в день без проблем.

Сильный ICP-сигнал: компания публикует вакансии → активно растёт → нужны
цифровые продукты. Особенно если вакансии разработчиков — но Stenvik как раз
предлагает альтернативу штату ("не нанимайте — получите готовый продукт").
"""
from __future__ import annotations

import logging
import time
from typing import Iterator

import httpx

from worker.hunter.sources.base import LeadHit, LeadSource


log = logging.getLogger(__name__)


_API = "https://api.hh.ru/employers"
_DETAIL = "https://api.hh.ru/employers/{eid}"

# HH area_id для основных городов (HH areas dictionary — стабильные id).
HH_AREAS = {
    "Москва":               1,
    "Санкт-Петербург":      2,
    "Краснодар":            53,
    "Казань":               88,
    "Новосибирск":          4,
    "Екатеринбург":         3,
    "Ростов-на-Дону":       76,
    "Самара":               78,
    "Уфа":                  99,
    "Челябинск":            104,
    "Нижний Новгород":      66,
    "Пермь":                72,
    "Воронеж":              26,
    "Волгоград":            24,
    "Красноярск":           54,
    "Тюмень":               95,
    "Сочи":                 1438,
    "Иркутск":              63,
    "Калининград":          41,
}


class HHEmployersSource(LeadSource):
    """Источник лидов из HH.ru Employers API.

    Бесплатный, без ключа. С 30-минутной частотой даёт ≥100 уникальных
    компаний/день со стабильной деdupлексацией.
    """
    name = "hh"

    def __init__(
        self,
        cities: list[str] | None = None,
        categories: list[str] | None = None,
        per_page: int = 50,
        max_pages_per_pair: int = 2,
        polite_delay: float = 0.4,
    ):
        # По умолчанию используем те же категории и города что и 2GIS
        from app.config import settings
        self.cities = cities or settings.twogis_cities_list
        self.categories = categories or settings.twogis_categories_list
        self.per_page = per_page
        self.max_pages_per_pair = max_pages_per_pair
        self.polite_delay = polite_delay
        # HH с 2024 требует именно HH-User-Agent (формат: AppName (contact_email)).
        # Под обычным User-Agent методы возвращают 403.
        self.headers = {
            "HH-User-Agent": "Stenvik (outreach@stenvik.studio)",
            "User-Agent": "Stenvik/1.0",
        }

    def iter_leads(self, *, limit: int = 50) -> Iterator[LeadHit]:
        emitted = 0
        with httpx.Client(timeout=15.0, headers=self.headers) as client:
            for category in self.categories:
                for city in self.cities:
                    if emitted >= limit:
                        return
                    area_id = HH_AREAS.get(city)
                    for page in range(self.max_pages_per_pair):
                        if emitted >= limit:
                            return
                        try:
                            data = self._search(client, category, area_id, page)
                        except Exception:  # noqa: BLE001
                            log.exception("hh search failed: %s/%s/p%d", category, city, page)
                            break
                        items = data.get("items", []) or []
                        if not items:
                            break
                        for item in items:
                            if emitted >= limit:
                                return
                            hit = self._employer_to_hit(client, item, category, city)
                            if hit:
                                yield hit
                                emitted += 1
                        time.sleep(self.polite_delay)

    def _search(self, client: httpx.Client, text: str, area_id: int | None, page: int) -> dict:
        params = {
            "text": text,
            "per_page": str(self.per_page),
            "page": str(page),
            "only_with_vacancies": "true",
        }
        if area_id is not None:
            params["area"] = str(area_id)
        r = client.get(_API, params=params)
        if r.status_code != 200:
            log.warning("hh search status=%s body=%s", r.status_code, r.text[:200])
            return {}
        return r.json()

    def _employer_to_hit(
        self, client: httpx.Client, item: dict, category: str, city: str,
    ) -> LeadHit | None:
        eid = item.get("id")
        name = (item.get("name") or "").strip()
        if not eid or not name:
            return None
        alt_url = item.get("alternate_url")  # https://hh.ru/employer/{id}
        site_url = (item.get("site_url") or "").strip() or None
        # Детальный запрос для получения site_url, если не пришёл
        if not site_url:
            try:
                r = client.get(_DETAIL.format(eid=eid))
                if r.status_code == 200:
                    detail = r.json()
                    site_url = (detail.get("site_url") or "").strip() or None
                    # из детального ответа можно вытащить industries[].name
                    industries = detail.get("industries") or []
                    industry_name = next(
                        (i.get("name") for i in industries),
                        None,
                    )
                    if industry_name:
                        category = industry_name
                time.sleep(self.polite_delay)
            except Exception:  # noqa: BLE001
                log.warning("hh detail fetch failed for %s", eid)

        return LeadHit(
            name=name,
            source="hh",
            source_id=str(eid),
            source_url=alt_url,
            website_url=site_url,
            city=city,
            industry=category,
            raw={
                "open_vacancies": item.get("open_vacancies"),
                "trusted": item.get("trusted"),
            },
        )
