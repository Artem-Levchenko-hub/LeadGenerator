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
        "recent_commits_24h": _recent_commits(),
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
            "hourly_observations_24h": _hourly_observations(db),
            "tunable_prompt_blocks": _tunable_prompt_blocks(),
        })
    return facts


def _hourly_observations(db) -> list[dict[str, Any]]:
    """Последние 24 observations от hourly collector — «память дня» для CEO."""
    cutoff = datetime.utcnow() - timedelta(hours=26)
    rows = (
        db.query(models.Observation)
        .filter(models.Observation.created_at >= cutoff)
        .order_by(models.Observation.id.desc())
        .limit(24)
        .all()
    )
    return [
        {
            "ts": r.created_at.isoformat() + "Z" if r.created_at else None,
            "kind": r.kind,
            "summary": r.summary,
        }
        for r in rows
    ]


# Файлы / блоки которые CEO имеет право предлагать к изменению. Вне
# whitelist'а — нельзя (не предлагать структурные правки кода, только
# тюнинг текста/правил).
_TUNABLE_BLOCKS = [
    {"file": "ai/prompts.py", "block": "STENVIK_PRODUCT_BRIEF",
     "purpose": "Прайс + ICP + горячие сигналы для агентов."},
    {"file": "ai/prompts.py", "block": "BUSINESS_GOAL",
     "purpose": "Цели машины — критерии legality + quality."},
    {"file": "ai/prompts.py", "block": "STENVIK_USP",
     "purpose": "УТП в подписи + закрытии."},
    {"file": "ai/prompts.py", "block": "HUMAN_VOICE_RULES",
     "purpose": "Запреты на ИИ-речь + примеры живой."},
    {"file": "ai/prompts.py", "block": "PAIN_TO_SOLUTION_MAP",
     "purpose": "Таблица боль → продукт Stenvik."},
    {"file": "ai/prompts.py", "block": "WEAKNESSES_TAXONOMY",
     "purpose": "Виды слабых мест сайта для record_weakness."},
    {"file": "ai/prompts.py", "block": "ANTI_HALLUCINATION_RULES",
     "purpose": "Что нельзя выдумывать (имена / email / метрики)."},
    {"file": "ai/prompts.py", "block": "OUTREACH_AGENT_SYSTEM",
     "purpose": "Роль и инструкции Outreach Agent (после ANTI_HALLUCINATION)."},
]


def _tunable_prompt_blocks() -> list[dict[str, Any]]:
    """Возвращает текущее содержимое блоков, которые CEO может тюнить.

    Парсим ai/prompts.py чтобы CEO видел что менять. Без этого он не
    может выдать diff — у него нет «before».
    """
    out: list[dict[str, Any]] = []
    for entry in _TUNABLE_BLOCKS:
        body = _read_block_body(entry["block"])
        out.append({
            "block": entry["block"],
            "file": entry["file"],
            "purpose": entry["purpose"],
            "current_chars": len(body) if body else 0,
        })
    return out


def _read_block_body(name: str) -> str | None:
    """Извлекает текст между тройными кавычками для NAME = ... = '''body'''.

    Покрывает оба паттерна:
      NAME = '''body'''
      NAME = OTHER + '''body'''   (пример: OUTREACH_AGENT_SYSTEM)
    """
    import re
    prompts_path = Path(__file__).resolve().parents[2] / "ai" / "prompts.py"
    if not prompts_path.exists():
        return None
    src = prompts_path.read_text(encoding="utf-8")
    # Левая часть: имя, потом = , потом любые символы НЕ-кавычки (чтобы не
    # съесть случайно начало другого литерала), потом тройная кавычка.
    pattern = rf'^{name}\s*=\s*[^"]*"""(.*?)"""'
    m = re.search(pattern, src, re.DOTALL | re.MULTILINE)
    return m.group(1) if m else None


def _recent_commits() -> list[dict[str, str]]:
    """Последние коммиты репо за 24ч — чтобы CEO не предлагал чинить уже починенное."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "log", "--since=24 hours ago", "--pretty=format:%h|%s", "-30"],
            capture_output=True, text=True, timeout=10, cwd=Path(__file__).resolve().parents[2],
        )
        if out.returncode != 0:
            return []
        result = []
        for line in out.stdout.strip().splitlines():
            if "|" in line:
                h, s = line.split("|", 1)
                result.append({"sha": h, "subject": s[:120]})
        return result
    except Exception:  # noqa: BLE001
        return []


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

Ты получаешь:
1. Снимок состояния машины (facts JSON — числа, агрегаты).
2. 24 наблюдения от hourly-наблюдателя (DeepSeek) — что происходило за день.
3. Текущий текст тюнабельных prompt-блоков для агентов (`ai/prompts.py`).

Твоя задача — НЕ просто отчёт. Ты должен **реально менять поведение
агентов**: переписывать их prompt-блоки на основе того что увидел в
поведении (например, Auditor режет 70% драфтов за отсутствие unsubscribe
→ ты усиливаешь это правило в OUTREACH_AGENT_SYSTEM, добавляешь чёткий
шаблон подписи).

Ты выдаёшь markdown-отчёт строго по структуре ниже. Структура — обязательная.

⚠️ ВАЖНО: смотри `recent_commits_24h` ДО того как делать рекомендации. Если
fix уже задеплоен — НЕ предлагай его снова. В таком случае пиши «починено в
коммите abc123, ждём 1-3 тика чтобы увидеть эффект».

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

# 🔧 Patches (РЕАЛЬНЫЕ ИЗМЕНЕНИЯ ПРОМПТОВ)

Это самая важная секция. Ты не просто советуешь — ты ПИШЕШЬ новые версии
prompt-блоков агентов, чтобы их поведение реально изменилось.

Можно тюнить только эти блоки в `ai/prompts.py` (тебе их полный текущий
текст приходит как attachment в user message):

- `STENVIK_PRODUCT_BRIEF` — прайс + ICP + горячие сигналы.
- `BUSINESS_GOAL` — цели машины, legality + quality критерии.
- `STENVIK_USP` — УТП в подписи + закрытии.
- `HUMAN_VOICE_RULES` — запреты на ИИ-речь + примеры живой.
- `PAIN_TO_SOLUTION_MAP` — таблица боль → продукт.
- `WEAKNESSES_TAXONOMY` — виды слабых мест сайта.
- `ANTI_HALLUCINATION_RULES` — что нельзя выдумывать.
- `OUTREACH_AGENT_SYSTEM` — роль Outreach Agent.

Каждый patch — это полная замена блока. НЕ выдавай diff. Выдавай НОВЫЙ
полный текст блока (то что должно стоять между тройными кавычками).

⚠️ Жёсткие правила, которые нельзя нарушать в новых версиях:
- В INNERTALK-секции (если блок её содержит) НИКОГДА не пиши о
  шифровании / E2E / encryption — Auditor блокирует.
- Подпись Stenvik в OUTREACH должна содержать слова `stenvik` и
  `unsubscribe` (Auditor проверяет).
- Не удаляй существующий контент бездумно — только улучшай или замещай.
- Не делай блок длиннее +50% от текущего размера (поедаешь токены каждого
  агентского вызова).

Формат секции:

## PATCH `<block_name>`

**Reason:** 1-2 предложения почему именно этот блок надо изменить
(опирается на конкретные observation/facts).

**Expected impact:** «<метрика> с <X> до <Y>» — что хотим увидеть в
следующем daily audit'е через 1-7 дней.

```text-block
<полный новый текст блока, без тройных кавычек, без префикса BLOCK_NAME =>
```

Можно делать 0-3 patches за один аудит. Лучше 1-2 точечных и обоснованных,
чем 5 поверхностных. Если данных мало для уверенного изменения — НЕ делай
patch, скажи об этом в Блокерах.

# 🎯 Proposals (директивы для Tactical Orchestrator + юзер-блокеры)

Машинопарсимый JSON блок. Это для вещей которые нельзя выразить как patch
prompt'а — конфигурации, новые источники, infra-задачи, юзер-сайдовые
блокеры (SMTP, домен, top-up балансов).

```json
{"proposals": [
  {
    "kind": "infra | new_source | ab_test | priority_shift | user_action",
    "payload": {
      "title": "Краткий заголовок (≤80 chars)",
      "instruction": "Что именно сделать. Если user_action — что владельцу подключить.",
      "files_to_touch": ["относительный/путь.py:line"]
    },
    "reason": "Почему это нужно — 1-2 предложения опираясь на facts.",
    "expected_impact": "Цифровая цель — 'снизить дубли с 100% до <50%' и т.п."
  }
]}
```

ПРАВИЛА:
- Числа только из facts/observations. Не выдумывай.
- 1-3 proposals — лучше меньше, но точнее.
- Стиль: business-tech без эмодзи в теле текста, эмодзи только в шапках секций.
- НЕ переписывай факты обратно в отчёт целиком — анализируй, не копируй.
"""


def _build_user_message(facts: dict[str, Any]) -> str:
    blocks_text = _read_tunable_blocks_full()
    return (
        "Снимок состояния машины (facts):\n\n```json\n"
        + json.dumps(facts, ensure_ascii=False, indent=2, default=str)
        + "\n```\n\n"
        + "Текущий текст тюнабельных prompt-блоков (для возможной замены):\n\n"
        + blocks_text
        + "\n\nПодготовь отчёт строго по структуре из system prompt. "
        + "Если решаешь делать PATCH — выдавай полный новый текст блока."
    )


def _read_tunable_blocks_full() -> str:
    """Возвращает текущий текст всех whitelisted блоков в markdown-обёртке."""
    parts: list[str] = []
    for entry in _TUNABLE_BLOCKS:
        body = _read_block_body(entry["block"]) or "(NOT FOUND in source)"
        parts.append(
            f"### {entry['block']}\n"
            f"_Назначение: {entry['purpose']}_  ·  _Размер: {len(body)} chars_\n\n"
            f"```text\n{body}\n```\n"
        )
    return "\n".join(parts)


# ============================================================
# Patches — извлечение и сохранение
# ============================================================

def _extract_patches(report_md: str) -> list[dict[str, str]]:
    """Достаёт ## PATCH `block_name` секции с новым текстом блока."""
    import re
    patches: list[dict[str, str]] = []
    # Паттерн: ## PATCH `BLOCK_NAME`\n...```text-block\n...```
    pattern = (
        r"##\s*PATCH\s*`?(?P<name>[A-Z_]+)`?\s*\n"
        r"(?P<meta>.*?)"
        r"```text-block\s*\n(?P<body>.*?)\n```"
    )
    allowed = {b["block"] for b in _TUNABLE_BLOCKS}
    for m in re.finditer(pattern, report_md, re.DOTALL):
        name = m.group("name").strip()
        if name not in allowed:
            log.warning("CEO: patch for non-whitelisted block %r ignored", name)
            continue
        # Извлекаем reason и expected_impact из meta-блока
        reason_m = re.search(r"\*\*Reason:\*\*\s*(.+?)(?=\*\*Expected impact:\*\*|\Z)", m.group("meta"), re.DOTALL)
        impact_m = re.search(r"\*\*Expected impact:\*\*\s*(.+?)(?=```text-block|\Z)", m.group("meta"), re.DOTALL)
        patches.append({
            "block_name": name,
            "new_text": m.group("body"),
            "reason": (reason_m.group(1).strip() if reason_m else "")[:1000],
            "expected_impact": (impact_m.group(1).strip() if impact_m else "")[:1000],
        })
    return patches


def _save_patches_as_proposals(patches: list[dict[str, str]]) -> list[int]:
    """Каждый patch сохраняем как StrategyProposal kind='prompt_patch'."""
    if not patches:
        return []
    ids: list[int] = []
    with SessionLocal() as db:
        for p in patches:
            row = models.StrategyProposal(
                kind="prompt_patch",
                payload={
                    "block_name": p["block_name"],
                    "file": "ai/prompts.py",
                    "new_text": p["new_text"],
                    "new_text_chars": len(p["new_text"]),
                },
                reason=p["reason"][:2000] or None,
                expected_impact=p["expected_impact"][:2000] or None,
                status="pending",
            )
            db.add(row)
            db.flush()
            ids.append(row.id)
        db.commit()
    return ids


# ============================================================
# Запуск аудита
# ============================================================

def _make_client(api_key: str):
    from openai import OpenAI
    import httpx

    http_kwargs: dict[str, Any] = {"timeout": 180.0}
    # CEO ходит на anthropic/claude-* через OpenRouter — НЕ RU-эндпоинт,
    # поэтому если задан http_proxy_url, его используем.
    if settings.http_proxy_url:
        http_kwargs["proxy"] = settings.http_proxy_url

    return OpenAI(
        api_key=api_key,
        base_url=settings.openrouter_base_url,
        http_client=httpx.Client(**http_kwargs),
    )


def _call_llm(api_key: str, user_msg: str, max_tokens: int, model: str):
    client = _make_client(api_key)
    return client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": CEO_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=max_tokens,
        extra_headers={
            "HTTP-Referer": "https://lead-generator.ru",
            "X-Title": "Stenvik CEO Audit",
        },
    )


# Цепочка fallback: пробуем cео_model, при 402 — переходим на следующую
# дешёвую. Это спасает прогон когда баланс OpenRouter низкий — лучше
# отчёт от Haiku чем никакого. В заголовке журнала видно какая
# реально использовалась.
_FALLBACK_MODELS = [
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-haiku-4.5",
    "deepseek/deepseek-chat",
]


def audit() -> dict[str, Any]:
    """Один аудит. Возвращает {'report_md', 'facts', 'usage', 'cost_usd', 'proposals_created', 'used_model', 'used_key'}."""
    from openai import APIStatusError

    facts = gather_facts()
    user_msg = _build_user_message(facts)

    primary_key = settings.ceo_openrouter_api_key or settings.openrouter_api_key
    if not primary_key:
        raise RuntimeError("Neither ceo_openrouter_api_key nor openrouter_api_key is set.")
    fallback_key = (
        settings.openrouter_api_key
        if (settings.ceo_openrouter_api_key
            and settings.openrouter_api_key
            and settings.ceo_openrouter_api_key != settings.openrouter_api_key)
        else None
    )

    # Модели в порядке предпочтения (без дубликатов).
    seen: set[str] = set()
    models_chain: list[str] = []
    for m in [settings.ceo_model] + _FALLBACK_MODELS:
        if m and m not in seen:
            models_chain.append(m)
            seen.add(m)

    keys_chain: list[tuple[str, str]] = [(primary_key, "ceo_key")]
    if fallback_key:
        keys_chain.append((fallback_key, "main_key_fallback"))

    log.info("CEO audit: trying models=%s, facts_size=%d chars", models_chain, len(user_msg))
    started = datetime.utcnow()

    resp = None
    used_model = None
    used_key_label = None
    last_402_message = None
    for model in models_chain:
        for key, key_label in keys_chain:
            try:
                resp = _call_llm(key, user_msg, max_tokens=4000, model=model)
                used_model = model
                used_key_label = key_label
                break
            except APIStatusError as e:
                if e.status_code == 402:
                    last_402_message = str(e)
                    log.warning(
                        "402 on model=%s via %s — trying next combination. (%s)",
                        model, key_label, str(e)[:140],
                    )
                    continue
                raise
        if resp is not None:
            break

    if resp is None:
        raise RuntimeError(
            f"All model/key combinations returned 402. Top up OpenRouter at "
            f"https://openrouter.ai/settings/credits. Last error: {last_402_message}"
        )

    finished = datetime.utcnow()
    if used_model != settings.ceo_model:
        log.warning(
            "CEO ran on fallback model %r (preferred: %r). Top up OpenRouter to use Opus.",
            used_model, settings.ceo_model,
        )
    report_md = resp.choices[0].message.content or ""
    usage = resp.usage
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details:
        cached = getattr(details, "cached_tokens", 0) or 0
    in_t = (getattr(usage, "prompt_tokens", 0) or 0) - cached
    out_t = getattr(usage, "completion_tokens", 0) or 0
    cost_usd = estimate_cost_usd(used_model, {
        "input_tokens": in_t,
        "output_tokens": out_t,
        "cache_read_input_tokens": cached,
    })

    # Парсим proposals + patches из отчёта.
    proposals = _extract_proposals(report_md)
    proposal_ids = _save_proposals(proposals)
    patches = _extract_patches(report_md)
    patch_ids = _save_patches_as_proposals(patches)

    # Сохраняем agent_run.
    record = AgentRunRecord(
        agent_kind="ceo",
        model=used_model,
        iterations=1,
        input_tokens=in_t,
        output_tokens=out_t,
        cache_read_tokens=cached,
        cost_usd=cost_usd,
        success=True,
        summary=f"audit: {len(proposals)} proposals + {len(patches)} patches (key={used_key_label})",
        started_at=started,
    )
    record.persist()

    # Журнал на диске — чтобы CEO мог в следующий раз увидеть свою историю.
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    ts = started.strftime("%Y%m%d-%H%M%S")
    journal_file = JOURNAL_DIR / f"audit-{ts}.md"
    model_note = used_model if used_model == settings.ceo_model else f"{used_model} (FALLBACK from {settings.ceo_model})"
    header = (
        f"# CEO Audit — {started.isoformat()}Z\n\n"
        f"- model: `{model_note}` (key: `{used_key_label}`)\n"
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
        "patches_created": len(patch_ids),
        "patch_ids": patch_ids,
        "journal_file": str(journal_file),
        "used_key": used_key_label,
        "used_model": used_model,
        "preferred_model": settings.ceo_model,
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
    used = result.get("used_model")
    pref = result.get("preferred_model")
    suffix = "" if used == pref else f"  [FALLBACK from {pref}]"
    print(f"  model: {used}{suffix}  (key: {result.get('used_key', '?')})")
    print(f"  cost: ${result['cost_usd']:.5f} (~RUB {result['cost_rub_approx']:.2f})")
    print(f"  tokens: in={result['usage']['input_tokens']} (cache_read={result['usage']['cache_read']}) out={result['usage']['output_tokens']}")
    print(f"  proposals created: {result['proposals_created']} (ids: {result['proposal_ids']})")
    print(f"  journal: {result['journal_file']}")
    print("=" * 70)
    print()
    print(result["report_md"])


def prepare_for_manual_audit() -> str:
    """Готовит markdown-пакет для копи-пейста в claude.ai (Opus 4.7).

    Когда у юзера есть Claude MAX подписка — Opus там бесплатный (в лимите),
    и платить через OpenRouter не нужно. Этот режим даёт ему ровно один
    markdown-файл со всем контекстом — копируй и вставляй в claude.ai.
    """
    facts = gather_facts()
    user_msg = _build_user_message(facts)
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    out_file = JOURNAL_DIR / f"prepared-{ts}.md"
    content = (
        f"# Prepared CEO audit prompt — {datetime.utcnow().isoformat()}Z\n\n"
        "Скопируй всё ниже до ====END==== в claude.ai (Opus 4.7).\n"
        "Полученный markdown-ответ сохрани как `data/ceo_journal/audit-{ts}.md` "
        "и применяй patches вручную (или через `python -m worker.agents.apply <id>`).\n\n"
        "---\n\n## SYSTEM\n\n"
        + CEO_SYSTEM_PROMPT
        + "\n\n## USER\n\n"
        + user_msg
        + "\n\n====END====\n"
    )
    out_file.write_text(content, encoding="utf-8")
    return str(out_file)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    arg = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if arg == "facts-only":
        print(json.dumps(gather_facts(), ensure_ascii=False, indent=2, default=str))
        return 0
    if arg == "prepare":
        path = prepare_for_manual_audit()
        print(f"Prepared markdown for claude.ai -> {path}")
        print("Скопируй содержимое файла до ====END==== в claude.ai (Opus 4.7).")
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
