"""Базовый интерфейс источника лидов.

Каждый источник реализует `iter_leads()` который возвращает поток `LeadHit`.
Hunter runner вызывает их и пишет в Company.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class LeadHit:
    """Сырая запись о найденной компании. Не привязана к БД-моделям."""
    name: str
    source: str                                # "2gis" | "hh" | ...
    source_id: str | None = None               # 2gis item id, hh employer id и т.д.
    source_url: str | None = None              # ссылка на карточку источника
    website_url: str | None = None             # если источник дал сайт
    city: str | None = None
    industry: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    raw: dict = field(default_factory=dict)    # сырые данные для архива

    def normalized_key(self) -> str:
        """Ключ для дедупа: lower-case name + city + source_id если есть."""
        parts = [
            (self.name or "").strip().lower(),
            (self.city or "").strip().lower(),
            self.source_id or "",
        ]
        return "|".join(parts)


class LeadSource:
    """Базовый класс источника. Подкласс должен переопределить iter_leads."""

    name: str = "base"

    def iter_leads(self, *, limit: int = 10) -> Iterable[LeadHit]:
        """Возвращает iterable LeadHit. Реализация в подклассах."""
        raise NotImplementedError
