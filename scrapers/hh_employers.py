"""HH.ru работодатели — публичный API, без ключа.

Документация: https://github.com/hhru/api/blob/master/docs/employers.md
"""
import logging
import time
from dataclasses import dataclass

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

HH_API_BASE = "https://api.hh.ru"
USER_AGENT = "StenvikLeadPipeline/1.0 (contact@stenvik.studio)"

AREA_NAMES = {
    1: "Москва",
    2: "Санкт-Петербург",
    3: "Екатеринбург",
    4: "Новосибирск",
    66: "Нижний Новгород",
    88: "Казань",
    53: "Краснодар",
    76: "Ростов-на-Дону",
    104: "Самара",
    54: "Красноярск",
    2019: "Москва и область",
    113: "Россия",
}


@dataclass
class HHEmployer:
    id: str
    name: str
    site_url: str | None
    description_html: str | None
    industries: list[dict]
    area_name: str | None
    alternate_url: str | None

    @property
    def industry_names(self) -> list[str]:
        return [i.get("name", "") for i in self.industries]

    @property
    def industry_ids(self) -> list[str]:
        return [str(i.get("id", "")) for i in self.industries]

    @property
    def top_level_industry_ids(self) -> list[str]:
        return [iid.split(".")[0] for iid in self.industry_ids]


class HHClient:
    def __init__(self, timeout: float = 15.0):
        self.client = httpx.Client(
            base_url=HH_API_BASE,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def list_employers(
        self,
        area: int | None = None,
        text: str | None = None,
        only_with_vacancies: bool = True,
        page: int = 0,
        per_page: int = 100,
    ) -> dict:
        """Returns dict with 'items', 'pages', 'page', 'per_page', 'found'."""
        params: dict = {
            "page": page,
            "per_page": per_page,
            "only_with_vacancies": str(only_with_vacancies).lower(),
        }
        if area is not None:
            params["area"] = area
        if text:
            params["text"] = text

        r = self.client.get("/employers", params=params)
        r.raise_for_status()
        return r.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get_employer(self, employer_id: str) -> HHEmployer | None:
        r = self.client.get(f"/employers/{employer_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()

        area_obj = data.get("area") or {}
        area_id = area_obj.get("id")
        try:
            area_id_int = int(area_id) if area_id is not None else None
        except (ValueError, TypeError):
            area_id_int = None
        area_name = area_obj.get("name") or AREA_NAMES.get(area_id_int) if area_id_int else None

        return HHEmployer(
            id=str(data["id"]),
            name=data.get("name", ""),
            site_url=(data.get("site_url") or "").strip() or None,
            description_html=data.get("description"),
            industries=data.get("industries", []),
            area_name=area_name,
            alternate_url=data.get("alternate_url"),
        )


def iter_employers_for_area(
    client: HHClient,
    area: int,
    max_pages: int = 20,
    per_page: int = 100,
    sleep_between: float = 0.3,
):
    """Yield employer ID + name summaries for an area, page by page."""
    for page in range(max_pages):
        try:
            data = client.list_employers(area=area, page=page, per_page=per_page)
        except httpx.HTTPStatusError as e:
            logger.warning("HH list failed area=%s page=%s: %s", area, page, e)
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            yield item

        if page + 1 >= data.get("pages", 0):
            break
        time.sleep(sleep_between)
