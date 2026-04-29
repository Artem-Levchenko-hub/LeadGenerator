"""Stub-рефакторинг build_kp.py для кросс-платформенного запуска (Linux/Win).

Полноценный рендер будет в Спринте 3+. Сейчас — минимальный API:
- `render_kp(ctx) -> bytes` — возвращает PDF.
- KPContext — TypedDict с полями для рендера.

ВАЖНО:
- Шрифты лежат в kp/fonts/ (DejaVu Sans, бесплатные кроссплатформенные).
  Скрипт `kp/_fonts_setup.py` скачивает их при первом запуске.
- Старый `D:\\Новая папка\\build_kp.py` использовал `C:/Windows/Fonts/` — это
  не работает на Linux. Здесь используется регистрация по локальному пути.
"""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict


FONTS_DIR = Path(__file__).resolve().parent / "fonts"


class KPContext(TypedDict, total=False):
    company_name: str
    industry: str
    pains: list[str]
    services: list[dict]            # [{name, price_rub, why}]
    cases: list[dict]               # [{name, url, summary, restrictions_text}]
    intro_text: str
    signer_name: str
    signer_email: str
    total_price_rub: float
    estimation_breakdown: list[dict]
    risks_text: str
    assumptions_text: str


def render_kp(ctx: KPContext) -> bytes:
    """Генерирует персонализированное КП в PDF и возвращает байты.

    Stub: пока не реализован — Спринт 3. Возвращает пустые байты, чтобы
    остальной код мог импортировать модуль без ошибок.
    """
    # TODO(Sprint 3): port build_kp.py here using DejaVu fonts from FONTS_DIR.
    raise NotImplementedError(
        "KP renderer is not implemented yet. Coming in Sprint 3 after fonts setup."
    )
