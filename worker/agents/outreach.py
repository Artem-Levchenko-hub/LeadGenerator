"""Outreach Agent — единый агент cold + reply.

Триггеры через agent_tasks:
- outreach.first_touch(company_id) — холодное касание после анализа сайта.
- outreach.continue(conversation_id, new_inbound_msg_id) — продолжение диалога.

⚠️ Передаём DeepSeek/любой LLM **реальные данные** из БД в user_message,
а не company_id — иначе модель будет ГАЛЛЮЦИНИРОВАТЬ названия и email-адреса.
"""
from __future__ import annotations

import json

from app.config import settings
from app.database import SessionLocal
from app import models
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


def _read_company_facts(company_id: int) -> dict | None:
    """Возвращает СТРОГО ФАКТИЧЕСКИЕ данные о компании из БД.

    Это всё что агент имеет право использовать — никаких выдуманных полей.
    """
    with SessionLocal() as db:
        c = db.query(models.Company).filter_by(id=company_id).one_or_none()
        if not c:
            return None
        return {
            "company_id": c.id,
            "name_real": c.name,
            "city_real": c.city,
            "industry_real": c.industry,
            "country": c.country,
            "website_url": c.website_url,
            "address": (c.contacts or {}).get("address"),
            "phone_known": (c.contacts or {}).get("phone"),
            "email_known": (c.contacts or {}).get("email"),
            "twogis_card_url": (c.contacts or {}).get("2gis_url"),
            "rubrics": ((c.contacts or {}).get("meta") or {}).get("rubrics"),
            "attributes": ((c.contacts or {}).get("meta") or {}).get("attributes"),
            "stage": c.stage,
        }


def run_first_touch(company_id: int, task_id: int | None = None) -> dict:
    """Запускает Outreach Agent в режиме холодного касания."""
    facts = _read_company_facts(company_id)
    if not facts:
        return {"success": False, "error": f"company {company_id} not found"}

    facts_json = json.dumps(facts, ensure_ascii=False, indent=2)

    user_message = f"""Задача: outreach.first_touch для компании #{company_id}.

⚠️ КРИТИЧЕСКОЕ ПРАВИЛО: ниже факты о компании из НАШЕЙ БД. Это ЕДИНСТВЕННЫЙ
источник правды. Используй ТОЛЬКО эти значения. НЕ ВЫДУМЫВАЙ названия,
адреса, email-адреса, описания услуг. Если поле = null или пусто — значит
этих данных у нас нет, и ты НЕ можешь их сочинить.

```json
{facts_json}
```

ПРАВИЛА РАБОТЫ:

1. В письме поле "Здравствуйте, ..." — пиши ТОЧНО `name_real` (как в БД).
   Не переименовывай в "Гармония", "ВашМедЦентр" и т.п.

2. Если `website_url` = null:
   • НЕ вызывай fetch_site без URL. Для нашего случая нет сайта — это сильный
     ICP-сигнал ("компания без сайта в 2GIS").
   • Можешь попробовать угадать домен через category+name (e.g. "stomatology-marino.ru")
     — НО ТОЛЬКО если хочешь проверить через dns_check. Если DNS не отвечает
     — НЕ выдумывай email на этом домене.
   • Если ни сайта, ни email_known — вызови escalate_to_human(reason="no_b2b_email_found")
     и завершись через finish. Лучше пропустить, чем спамить выдуманный адрес.

3. Если `website_url` задан:
   • fetch_site(url=website_url) → читай результат.
   • dns_check(domain=...) на домене из website_url.
   • Из контактов (contacts.emails_corporate в ответе fetch_site) бери to_address.
   • Если в contacts.emails_corporate пусто, а есть emails_personal — НЕ шли
     на personal (Auditor заблокирует). Пробуй info@<домен> через dns_check.
   • Если всё равно не нашлось B2B-email — escalate_to_human + finish.

4. Записывай 2-5 weakness через record_weakness. Используй ТОЧНЫЕ kind из таксономии.

5. Драфт письма через draft_message(channel='email', to_address=<реальный B2B email>).
   • body должен начинаться с реального названия из `name_real`.
   • НЕ упоминай факты которых нет в `facts` (количество филиалов, годы, имена врачей и т.п.).
   • Подпись Stenvik с email и сайтом, opt-out обязателен.

6. ВСЕГДА завершайся через finish(summary="...").

Начинай работу. Сначала вызови ОДИН инструмент (fetch_site если есть website_url,
иначе сразу dns_check на угадываемом домене или escalate_to_human)."""

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
    facts_json = "{}"
    if company_id:
        facts = _read_company_facts(company_id)
        if facts:
            facts_json = json.dumps(facts, ensure_ascii=False, indent=2)

    user_message = f"""Задача: outreach.continue для conversation_id={conversation_id}.

⚠️ Используй ТОЛЬКО факты из БД ниже. НЕ выдумывай ничего.

```json
{facts_json}
```

ПРАВИЛА:
1. Сначала read_thread(conversation_id={conversation_id}) — прочитай ВСЮ историю.
2. Прочитав thread — реши:
   • Лид готов к BANT-квалификации/обсуждению КП → update_conversation_state(state='engaged') и escalate_to_human(reason='ready_for_sales').
   • Лид задаёт вопрос → ответь через draft_message с тем же channel что и thread.
   • Лид сливается → update_conversation_state(state='lost'), finish.
3. Loop guard: посчитай в thread количество ботских реплик подряд без человеческого
   ответа. Если ≥5 — escalate_to_human(reason='loop_guard') без отправки.
4. ВСЕГДА завершайся через finish."""

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
