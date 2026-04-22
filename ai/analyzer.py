"""Claude Sonnet анализирует компанию и формирует лид-анализ для продажника.

Используем prompt caching на системном промпте — экономит ~90% на повторных запросах.
Структурированный вывод через client.messages.parse() + Pydantic.
"""
import logging
from typing import Literal

import anthropic
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from ai.prompts import STENVIK_CONTEXT

logger = logging.getLogger(__name__)


class LeadAnalysis(BaseModel):
    summary: str = Field(description="Что за компания — 1-2 предложения")
    pains: list[str] = Field(description="3-5 конкретных болей", min_length=1, max_length=6)
    recommended_services: list[str] = Field(
        description="1-3 услуги Stenvik, подходящих под боль",
        min_length=1, max_length=4,
    )
    sales_hook: str = Field(description="Персональный хук для продажника, 2-3 предложения")
    priority: Literal[1, 2, 3, 4, 5] = Field(description="1 — не наш, 5 — идеальный лид")
    priority_reason: str = Field(description="Почему такой приоритет, 1-2 предложения")


def _build_user_prompt(
    *,
    company_name: str,
    industry: str | None,
    city: str | None,
    description_hh: str | None,
    website_url: str | None,
    website_status: str,
    website_title: str | None,
    website_description: str | None,
    website_text: str | None,
    website_stack: list[str] | None,
) -> str:
    parts: list[str] = [
        f"## Компания: {company_name}",
        f"**Индустрия:** {industry or 'не указана'}",
        f"**Город:** {city or 'не указан'}",
    ]

    if description_hh:
        parts.append(f"\n**Описание компании с HH.ru:**\n{description_hh[:2000]}")
    else:
        parts.append("\n**Описание с HH.ru:** отсутствует")

    parts.append(f"\n**Статус сайта:** {website_status}")

    if website_status == "no_site":
        parts.append(
            "У компании НЕТ сайта вообще (поиск не нашёл, в профиле HH пусто). "
            "Это сильный сигнал — компания существует, нанимает людей, "
            "но не присутствует в digital-пространстве."
        )
    elif website_url:
        parts.append(f"**URL:** {website_url}")
        if website_title:
            parts.append(f"**Title:** {website_title}")
        if website_description:
            parts.append(f"**Meta description:** {website_description}")
        if website_stack:
            parts.append(f"**Определённый стек/CMS:** {', '.join(website_stack)}")
        if website_text:
            parts.append(f"\n**Текст с главной страницы (обрезан):**\n{website_text[:3500]}")

    parts.append(
        "\n---\n"
        "Проанализируй эту компанию как потенциального клиента Stenvik и выдай структурированный ответ."
    )
    return "\n".join(parts)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
def analyze_company(
    *,
    company_name: str,
    industry: str | None,
    city: str | None,
    description_hh: str | None,
    website_url: str | None,
    website_status: str,
    website_title: str | None = None,
    website_description: str | None = None,
    website_text: str | None = None,
    website_stack: list[str] | None = None,
    client: anthropic.Anthropic | None = None,
) -> LeadAnalysis:
    """Анализирует компанию и возвращает LeadAnalysis."""
    if client is None:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    user_prompt = _build_user_prompt(
        company_name=company_name,
        industry=industry,
        city=city,
        description_hh=description_hh,
        website_url=website_url,
        website_status=website_status,
        website_title=website_title,
        website_description=website_description,
        website_text=website_text,
        website_stack=website_stack,
    )

    response = client.messages.parse(
        model=settings.claude_model,
        max_tokens=2000,
        system=[{
            "type": "text",
            "text": STENVIK_CONTEXT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_prompt}],
        output_format=LeadAnalysis,
    )

    logger.info(
        "Claude usage: input=%s cache_read=%s cache_write=%s output=%s",
        response.usage.input_tokens,
        response.usage.cache_read_input_tokens,
        response.usage.cache_creation_input_tokens,
        response.usage.output_tokens,
    )

    return response.parsed_output
