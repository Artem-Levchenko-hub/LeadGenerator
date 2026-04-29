"""Outreach Agent — единый агент cold + reply.

Триггеры через agent_tasks:
- outreach.first_touch(company_id) — холодное касание после анализа сайта.
- outreach.continue(conversation_id, new_inbound_msg_id) — продолжение диалога.
"""
from __future__ import annotations

from app.config import settings
from ai import prompts
from worker.llm import run_react_loop
from worker.agents.tools.definitions import OUTREACH_TOOLS
from worker.agents.tools.handlers import OUTREACH_HANDLERS


def _system_blocks() -> list[dict]:
    """Общий cached prefix + ролевой промпт Outreach."""
    return [
        {"type": "text", "text": prompts.build_common_prefix()},
        {"type": "text", "text": prompts.OUTREACH_AGENT_SYSTEM},
    ]


def run_first_touch(company_id: int, task_id: int | None = None) -> dict:
    """Запускает Outreach Agent в режиме холодного касания."""
    user_message = (
        f"Запусти `outreach.first_touch` для company_id={company_id}.\n\n"
        "Шаги:\n"
        "1. Прочитай Company через update_company с пустым fields={} — нет, "
        "лучше: используй другие источники (industry/city/website_url передаю ниже).\n"
        f"\nданные о компании читай через record_weakness/draft_message — они уже знают company_id={company_id}."
        "\n\nЕсли есть website_url — сначала fetch_site, потом dns_check, по необходимости whois_lookup."
        "\nЗаписывай каждое найденное слабое место через record_weakness."
        "\nКогда всё проанализировано — draft_message с email-телом по структуре из system."
        "\nНе забудь: company_id во всех tools — это число выше."
        "\nЗакончи через finish(summary=...)."
    )
    record = run_react_loop(
        agent_kind="outreach",
        system_blocks=_system_blocks(),
        user_message=user_message,
        tools=OUTREACH_TOOLS,
        tool_handlers=OUTREACH_HANDLERS,
        model=settings.model_default,
        max_iterations=settings.outreach_max_iterations,
        task_id=task_id,
        company_id=company_id,
    )
    return {
        "success": record.success,
        "summary": record.summary,
        "iterations": record.iterations,
        "cost_usd": record.cost_usd,
        "error": record.error_text,
    }


def run_continue(
    conversation_id: int,
    company_id: int | None = None,
    task_id: int | None = None,
) -> dict:
    """Запускает Outreach Agent для ответа в треде."""
    user_message = (
        f"Запусти `outreach.continue` для conversation_id={conversation_id}.\n\n"
        f"company_id={company_id}.\n\n"
        "Шаги:\n"
        f"1. read_thread(conversation_id={conversation_id}) — прочитай всю историю.\n"
        "2. Реши: ответить / эскалировать / передать в продажи (state=engaged).\n"
        "3. Если отвечаешь — draft_message с conversation_id и тем же channel что и thread.\n"
        "4. Loop guard: 5+ ботских реплик без ответа → escalate_to_human.\n"
        "5. finish(summary=...)."
    )
    record = run_react_loop(
        agent_kind="outreach",
        system_blocks=_system_blocks(),
        user_message=user_message,
        tools=OUTREACH_TOOLS,
        tool_handlers=OUTREACH_HANDLERS,
        model=settings.model_default,
        max_iterations=settings.outreach_max_iterations,
        task_id=task_id,
        company_id=company_id,
    )
    return {
        "success": record.success,
        "summary": record.summary,
        "iterations": record.iterations,
        "cost_usd": record.cost_usd,
        "error": record.error_text,
    }
