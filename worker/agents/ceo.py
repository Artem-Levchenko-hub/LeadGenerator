"""Strategic Orchestrator — «CEO Stenvik».

Задача: ежедневно (или по требованию) аудит-ить состояние outbound-машины
и выдавать владельцу:
1. Что работает — последние победы.
2. Блокеры роста — что мешает прямо сейчас, в порядке приоритета.
3. Архитектурные рекомендации — что менять в коде в ближайшие дни.
4. Цена и экономика — где горит бюджет, где недогружено.
5. ConcreteProposals — машина-парсимый список изменений (kind/payload/...)
   которые сохраняются как `StrategyProposal` со статусом 'pending'. Владелец
   одобряет/отклоняет на UI; одобрённые становятся `StrategyDirective` и их
   читает Tactical Orchestrator при принятии решений.

CEO не делает ReAct loop'ы и не дёргает tools — это одна-единственная
LLM-итерация, потому что (a) Opus дорогой, (b) задача — отчёт + предложения,
а не разветвлённый план действий с tool use.

Запуск:
    .venv/Scripts/python.exe -m worker.agents.ceo audit
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import settings
from app.database import SessionLocal
from app import models
from worker.llm import AgentRunRecord, estimate_cost_usd


log = logging.getLogger(__name__)


JOURNAL_DIR = Path("data/ceo_journal")


# ============================================================
# Сбор фактов из БД и .env
# ============================================================

def gather_facts() -> dict[str, Any]:
    """Снимок состояния машины. Чисто read-only."""
    facts: dict[str, Any] = {
        "snapshot_at": datetime.utcnow().isoformat() + "Z",
        "settings": _settings_capabilities(),
    }
    with SessionLocal() as db:
        facts.update({
            "companies": _company_funnel(db),
            "hunter": _hunter_health(db),
            "outbox": _outbox_health(db),
            "agent_runs": _agent_runs_summary(db),
            "conversations": _conversation_funnel(db),
            "active_directives": _active_directives(db),
            "previous_proposals": _recent_proposals(db),
        })
    return facts


def _settings_capabilities() -> dict[str, Any]:
    """Какие каналы / лимиты сконфигурированы (без секретов)."""
    return {
        "smtp_configured": bool(settings.smtp_host and settings.smtp_password),
        "imap_configured": bool(settings.imap_host and settings.imap_password),
        "telegram_configured": bool(settings.telegram_bot_token),
        "sms_configured": bool(settings.smsc_login),
        "voice_configured": bool(settings.zvonok_api_key),
        "twogis_configured": bool(settings.twogis_api_key),
        "llm_provider": settings.effective_provider,
        "model_default": settings.model_default,
        "model_premium": settings.model_premium,
        "ceo_model": settings.ceo_model,
        "daily_llm_budget_usd": settings.daily_llm_budget_usd,
        "daily_email_limit": settings.daily_email_limit,
        "daily_telegram_limit": settings.daily_telegram_limit,
        "outbox_holding_seconds": settings.outbox_holding_seconds,
        "outreach_max_iterations": settings.outreach_max_iterations,
        "twogis_categories": settings.twogis_categories_list,
        "twogis_cities": settings.twogis_cities_list,
    }


def _company_funnel(db) -> dict[str, Any]:
    total = db.query(models.Company).count()
    by_stage: dict[str, int] = {}
    for c in db.query(models.Company.stage, models.Company.id).all():
        by_stage[c.stage or "unknown"] = by_stage.get(c.stage or "unknown", 0) + 1
    with_site = db.query(models.Company).filter(models.Company.website_url.isnot(None)).count()
    needs_human = db.query(models.Company).filter(models.Company.needs_human == True).count()  # noqa: E712
    cutoff = datetime.utcnow() - timedelta(hours=24)
    last_24h = db.query(models.Company).filter(models.Company.created_at >= cutoff).count()
    # Распределение по industry
    by_industry: dict[str, int] = {}
    for row in db.query(models.Company.industry).all():
        k = row[0] or "unknown"
        by_industry[k] = by_industry.get(k, 0) + 1
    return {
        "total": total,
        "with_website_url": with_site,
        "without_website_url": total - with_site,
        "needs_human": needs_human,
        "added_last_24h": last_24h,
        "by_stage": by_stage,
        "by_industry": dict(sorted(by_industry.items(), key=lambda x: -x[1])[:10]),
    }


def _hunter_health(db) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(hours=24)
    runs = (
        db.query(models.RunLog)
        .filter(models.RunLog.started_at >= cutoff)
        .order_by(models.RunLog.id.desc())
        .limit(50)
        .all()
    )
    total_seen = total_created = total_dup = total_errors = 0
    for r in runs:
        details = r.details or ""
        # парсим "seen=10 created=0 dup=10 errors=0"
        m = re.search(r"seen=(\d+).*created=(\d+).*dup=(\d+).*errors=(\d+)", details)
        if m:
            total_seen += int(m.group(1))
            total_created += int(m.group(2))
            total_dup += int(m.group(3))
            total_errors += int(m.group(4))
    last = runs[0] if runs else None
    return {
        "ticks_last_24h": len(runs),
        "leads_seen_24h": total_seen,
        "leads_created_24h": total_created,
        "duplicates_24h": total_dup,
        "errors_24h": total_errors,
        "dup_rate_pct": round(total_dup / total_seen * 100, 1) if total_seen else None,
        "last_tick_at": last.started_at.isoformat() + "Z" if last else None,
        "last_tick_details": (last.details or "")[:200] if last else None,
    }


def _outbox_health(db) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for row in db.query(models.OutboxMessage.status).all():
        k = row[0] or "unknown"
        by_status[k] = by_status.get(k, 0) + 1
    cutoff = datetime.utcnow() - timedelta(hours=24)
    rejected_24h = (
        db.query(models.OutboxMessage)
        .filter(
            models.OutboxMessage.status == "rejected",
            models.OutboxMessage.created_at >= cutoff,
        )
        .all()
    )
    rejection_reasons: dict[str, int] = {}
    for m in rejected_24h:
        notes = m.audit_notes or "unknown"
        # Извлекаем [tag] из "[opt_out_present] no opt-out..."
        rmatch = re.search(r"\[([^\]]+)\]", notes)
        reason = rmatch.group(1) if rmatch else notes[:40]
        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
    failed_24h = (
        db.query(models.OutboxMessage)
        .filter(
            models.OutboxMessage.status == "failed",
            models.OutboxMessage.created_at >= cutoff,
        )
        .count()
    )
    return {
        "total_by_status": by_status,
        "rejected_24h": len(rejected_24h),
        "rejection_reasons_24h": dict(sorted(rejection_reasons.items(), key=lambda x: -x[1])),
        "failed_24h": failed_24h,
    }


def _agent_runs_summary(db) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(hours=24)
    runs = (
        db.query(models.AgentRun)
        .filter(models.AgentRun.started_at >= cutoff)
        .all()
    )
    total = len(runs)
    success = sum(1 for r in runs if r.success)
    sum_in = sum(r.input_tokens or 0 for r in runs)
    sum_out = sum(r.output_tokens or 0 for r in runs)
    sum_cache = sum(r.cache_read_tokens or 0 for r in runs)
    sum_cost = sum(r.cost_usd or 0 for r in runs)
    by_kind: dict[str, dict[str, Any]] = {}
    for r in runs:
        k = r.agent_kind or "unknown"
        d = by_kind.setdefault(k, {"runs": 0, "cost_usd": 0.0, "input_tokens": 0})
        d["runs"] += 1
        d["cost_usd"] += r.cost_usd or 0
        d["input_tokens"] += r.input_tokens or 0
    for k in by_kind:
        by_kind[k]["cost_usd"] = round(by_kind[k]["cost_usd"], 4)
    return {
        "runs_24h": total,
        "success_rate_pct": round(success / total * 100, 1) if total else None,
        "input_tokens_24h": sum_in,
        "output_tokens_24h": sum_out,
        "cache_read_tokens_24h": sum_cache,
        "cache_hit_rate_pct": round(sum_cache / (sum_in + sum_cache) * 100, 1) if (sum_in + sum_cache) else None,
        "cost_usd_24h": round(sum_cost, 4),
        "cost_rub_24h_approx": round(sum_cost * 92, 2),
        "by_kind_24h": by_kind,
    }


def _conversation_funnel(db) -> dict[str, Any]:
    by_state: dict[str, int] = {}
    for row in db.query(models.Conversation.state).all():
        k = row[0] or "unknown"
        by_state[k] = by_state.get(k, 0) + 1
    return {"total": sum(by_state.values()), "by_state": by_state}


def _active_directives(db) -> list[dict[str, Any]]:
    rows = (
        db.query(models.StrategyDirective)
        .filter(models.StrategyDirective.current_status == "active")
        .all()
    )
    return [
        {
            "id": d.id,
            "kind": d.kind,
            "payload": d.payload,
            "active_from": d.active_from.isoformat() + "Z" if d.active_from else None,
        }
        for d in rows
    ]


def _recent_proposals(db) -> list[dict[str, Any]]:
    rows = (
        db.query(models.StrategyProposal)
        .order_by(models.StrategyProposal.id.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "id": p.id,
            "kind": p.kind,
            "status": p.status,
            "reason": (p.reason or "")[:200],
            "created_at": p.created_at.isoformat() + "Z" if p.created_at else None,
        }
        for p in rows
    ]


# ============================================================
# CEO Prompt
# ============================================================

CEO_SYSTEM_PROMPT = """Ты — CEO/стратегический оркестратор студии разработки **Stenvik**.

Цель машины — автономно находить B2B-клиентов на разработку ПО, вести с ними
диалог и доводить до подписания договора. Полный pipeline:
Hunter → Enrichment → Outreach Agent → Outbox + Auditor → SMTP →
Inbound IMAP → Sales Manager (BANT) → Discovery → Estimation → Proposal.

Ты получаешь снимок состояния машины (facts JSON) и выдаёшь markdown-отчёт
строго по структуре ниже. Структура — обязательная, шапку каждой секции
не меняй (на её основе UI парсит отчёт).

# 🟢 Что работает

3-5 пунктов о свежих победах. Конкретно — что улучшилось за 24 часа,
с цифрами из facts. Если ничего не улучшилось — так и пиши.

# 🔴 Блокеры роста

3-7 пунктов в порядке приоритета. Для каждого блокера:
- Симптом в цифрах из facts.
- Корневая причина (если можешь определить).
- Что делать: либо «владельцу подключить X» (для юзер-сайдовых блокеров —
  SMTP, домен, Telegram-бот), либо «агенту изменить Y» (для код-фиксов).

# 🛠️ Архитектурные рекомендации (для разработчика)

3-5 рекомендаций по коду в порядке impact/effort. Каждая:
- Конкретный файл (`worker/agents/outreach.py`, `ai/prompts.py:120` и т.п.).
- Что изменить.
- Ожидаемая метрика-победа (как измерим успех).

# 💰 Цена и экономика

Диагноз стоимости: куда горят токены, какой cache hit-rate, какая
средняя цена лида. Что дешевле / дороже, чем должно. 2-4 пункта.

# 🎯 Proposals

В конце — машинопарсимый JSON-блок предложений для StrategyProposal. Каждое
предложение — конкретная директива которую владелец одобрит или отклонит.

```json
{"proposals": [
  {
    "kind": "directive | new_source | ab_test | priority_shift | infra | content",
    "payload": {
      "title": "Краткий заголовок (≤80 chars)",
      "instruction": "Что именно изменить. Пиши как будто это инструкция инженеру.",
      "files_to_touch": ["относительный/путь.py:line"]
    },
    "reason": "Почему это нужно — 1-2 предложения, опираясь на facts.",
    "expected_impact": "Цифровая цель — 'снизить дубли с 100% до <50%' / 'добавить +20 leads/day' / 'cost/lead с ₽5 до ₽2'."
  }
]}
```

ПРАВИЛА:
- Числа только из facts. Не выдумывай.
- 1-3 proposals — лучше меньше, но точнее.
- Если данные слишком тонкие для рекомендаций (например только что запущена
  машина, мало истории) — честно скажи это в "Блокерах роста" и дай
  proposal "wait_and_observe" с reason="недостаточно данных".
- Стиль: business-tech без эмодзи в теле текста, эмодзи только в шапках секций.
- НЕ переписывай факты обратно в отчёт целиком — анализируй, не копируй.
"""


def _build_user_message(facts: dict[str, Any]) -> str:
    return (
        "Снимок состояния машины (facts):\n\n```json\n"
        + json.dumps(facts, ensure_ascii=False, indent=2, default=str)
        + "\n```\n\nПодготовь отчёт строго по структуре из system prompt."
    )


# ============================================================
# Запуск аудита
# ============================================================

def audit() -> dict[str, Any]:
    """Один аудит. Возвращает {'report_md', 'facts', 'usage', 'cost_usd', 'proposals_created'}."""
    facts = gather_facts()
    user_msg = _build_user_message(facts)

    api_key = settings.ceo_openrouter_api_key or settings.openrouter_api_key
    if not api_key:
        raise RuntimeError("Neither ceo_openrouter_api_key nor openrouter_api_key is set.")

    from openai import OpenAI
    import httpx

    http_kwargs: dict[str, Any] = {"timeout": 180.0}
    # CEO ходит на anthropic/claude-* через OpenRouter — НЕ российский эндпоинт,
    # поэтому если задан http_proxy_url, его используем.
    if settings.http_proxy_url:
        http_kwargs["proxy"] = settings.http_proxy_url

    client = OpenAI(
        api_key=api_key,
        base_url=settings.openrouter_base_url,
        http_client=httpx.Client(**http_kwargs),
    )

    log.info("CEO audit: model=%s, facts_size=%d chars", settings.ceo_model, len(user_msg))
    started = datetime.utcnow()
    resp = client.chat.completions.create(
        model=settings.ceo_model,
        messages=[
            {"role": "system", "content": CEO_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=4000,
        extra_headers={
            "HTTP-Referer": "https://lead-generator.ru",
            "X-Title": "Stenvik CEO Audit",
        },
    )
    finished = datetime.utcnow()
    report_md = resp.choices[0].message.content or ""
    usage = resp.usage
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details:
        cached = getattr(details, "cached_tokens", 0) or 0
    in_t = (getattr(usage, "prompt_tokens", 0) or 0) - cached
    out_t = getattr(usage, "completion_tokens", 0) or 0
    cost_usd = estimate_cost_usd(settings.ceo_model, {
        "input_tokens": in_t,
        "output_tokens": out_t,
        "cache_read_input_tokens": cached,
    })

    # Парсим proposals из отчёта.
    proposals = _extract_proposals(report_md)
    proposal_ids = _save_proposals(proposals)

    # Сохраняем agent_run.
    record = AgentRunRecord(
        agent_kind="ceo",
        model=settings.ceo_model,
        iterations=1,
        input_tokens=in_t,
        output_tokens=out_t,
        cache_read_tokens=cached,
        cost_usd=cost_usd,
        success=True,
        summary=f"audit produced {len(proposals)} proposals",
        started_at=started,
    )
    record.persist()

    # Журнал на диске — чтобы CEO мог в следующий раз увидеть свою историю.
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    ts = started.strftime("%Y%m%d-%H%M%S")
    journal_file = JOURNAL_DIR / f"audit-{ts}.md"
    header = (
        f"# CEO Audit — {started.isoformat()}Z\n\n"
        f"- model: `{settings.ceo_model}`\n"
        f"- input_tokens: {in_t} (cache_read: {cached})\n"
        f"- output_tokens: {out_t}\n"
        f"- cost_usd: {cost_usd:.5f} (~₽{cost_usd*92:.2f})\n"
        f"- proposals_created: {len(proposal_ids)} (ids: {proposal_ids})\n"
        f"- duration: {(finished - started).total_seconds():.1f}s\n\n"
        "---\n\n"
    )
    journal_file.write_text(header + report_md + "\n", encoding="utf-8")
    log.info("CEO audit saved: %s", journal_file)

    return {
        "report_md": report_md,
        "facts": facts,
        "usage": {
            "input_tokens": in_t, "output_tokens": out_t, "cache_read": cached,
        },
        "cost_usd": cost_usd,
        "cost_rub_approx": cost_usd * 92,
        "proposals_created": len(proposal_ids),
        "proposal_ids": proposal_ids,
        "journal_file": str(journal_file),
    }


def _extract_proposals(report_md: str) -> list[dict[str, Any]]:
    """Достаёт JSON блок с proposals из markdown-отчёта."""
    # Ищем ```json {"proposals": [...]} ```
    m = re.search(r"```json\s*(\{.*?\"proposals\".*?\})\s*```", report_md, re.DOTALL)
    if not m:
        # fallback: попробуем найти просто {"proposals": [...]}
        m = re.search(r'(\{\s*"proposals"\s*:\s*\[.*?\]\s*\})', report_md, re.DOTALL)
    if not m:
        log.warning("CEO: no proposals JSON found in report")
        return []
    try:
        data = json.loads(m.group(1))
        return data.get("proposals", []) or []
    except json.JSONDecodeError:
        log.exception("CEO: failed to parse proposals JSON")
        return []


def _save_proposals(proposals: list[dict[str, Any]]) -> list[int]:
    """Создаёт строки в strategy_proposals со status='pending'."""
    if not proposals:
        return []
    ids: list[int] = []
    with SessionLocal() as db:
        for p in proposals:
            row = models.StrategyProposal(
                kind=str(p.get("kind", "directive"))[:32],
                payload=p.get("payload") or {},
                reason=(p.get("reason") or "")[:2000] or None,
                expected_impact=(p.get("expected_impact") or "")[:2000] or None,
                status="pending",
            )
            db.add(row)
            db.flush()
            ids.append(row.id)
        db.commit()
    return ids


# ============================================================
# CLI
# ============================================================

def _print_summary(result: dict[str, Any]) -> None:
    print("=" * 70)
    print(f"CEO Audit completed.")
    print(f"  model: {settings.ceo_model}")
    print(f"  cost: ${result['cost_usd']:.5f} (~₽{result['cost_rub_approx']:.2f})")
    print(f"  tokens: in={result['usage']['input_tokens']} (cache_read={result['usage']['cache_read']}) out={result['usage']['output_tokens']}")
    print(f"  proposals created: {result['proposals_created']} (ids: {result['proposal_ids']})")
    print(f"  journal: {result['journal_file']}")
    print("=" * 70)
    print()
    print(result["report_md"])


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if len(sys.argv) > 1 and sys.argv[1] == "facts-only":
        print(json.dumps(gather_facts(), ensure_ascii=False, indent=2, default=str))
        return 0
    try:
        result = audit()
    except Exception as e:  # noqa: BLE001
        log.exception("CEO audit failed: %s", e)
        return 1
    _print_summary(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
