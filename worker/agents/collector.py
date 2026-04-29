"""Hourly Observer Collector — DeepSeek собирает наблюдения каждый час.

Цель: дать CEO (Opus 4.7) на следующий ручной аудит «память» о том, что
происходило в течение дня — не только cumulative агрегаты, но 24
точечных snapshot'а с короткими summary и аномалиями.

Каждый час collector:
1. Берёт snapshot за последний час (delta vs предыдущий snapshot если есть).
2. Передаёт DeepSeek facts с инструкцией «опиши 1-3 предложения что
   изменилось, отметь аномалии — 0 новых лидов, всплеск отбраковки в
   outbox, рост стоимости и т.д.».
3. Пишет в `observations` таблицу: kind, summary, payload, cost_usd.

Стоимость: ~$0.001/run × 24 = $0.024/day ≈ ₽2.2/day. Не критично.

Запуск:
    .venv/Scripts/python.exe -m worker.agents.collector run     # один раз
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from typing import Any

from app.config import settings
from app.database import SessionLocal
from app import models
from worker.llm import AgentRunRecord, estimate_cost_usd


log = logging.getLogger(__name__)


# Дешёвая модель — DeepSeek через OpenRouter. Если 402 — не fallback'имся
# на более дорогие, просто пишем naive snapshot без LLM-summary.
COLLECTOR_MODEL = "deepseek/deepseek-chat"


COLLECTOR_SYSTEM_PROMPT = """Ты — наблюдатель за outbound-машиной Stenvik.

Каждый час ты получаешь snapshot статистики (новые лиды за час, отбраковки
outbox, стоимость LLM, ошибки Hunter и т.д.) и предыдущий snapshot.

Твоя ЕДИНСТВЕННАЯ задача — написать **2-4 предложения** на русском в стиле
короткого лога, что произошло за последний час и есть ли аномалии.

Примеры хорошего summary:
- «Hunter добавил 5 новых компаний (4 со website_url из enrichment), всё в норме. Outreach обработал 5 лидов, 4 драфта одобрены Auditor'ом.»
- «АНОМАЛИЯ: Hunter 0 created при seen=10 — все дубли. Возможно exhausted (cat × city), нужна ротация. Outreach idle.»
- «Стоимость за час $0.08, кэш-хит 42% — норма. 1 SMTP fail из-за not configured (юзер блокер).»

ПРАВИЛА:
- Без markdown-заголовков, без эмодзи.
- Числа только из facts. Если число = 0, то «0», не «нет».
- Фокус на ИЗМЕНЕНИЕ vs предыдущий час, а не повтор cumulative цифр.
- Если данных мало (только что развёрнут / первый snapshot) — так и пиши.
- Если видишь явную аномалию (0 leads at peak hour, спайк rejection,
  cost explosion) — начни с «АНОМАЛИЯ:».

Никаких рекомендаций, никаких списков, никакого «следует сделать» —
только факт-лог. Рекомендации даёт CEO на daily audit.
"""


def gather_hourly_facts() -> dict[str, Any]:
    """Snapshot за последний час + delta vs предыдущий snapshot."""
    now = datetime.utcnow()
    hour_ago = now - timedelta(hours=1)
    facts: dict[str, Any] = {"window_start": hour_ago.isoformat() + "Z", "window_end": now.isoformat() + "Z"}
    with SessionLocal() as db:
        # Companies — добавлено за час
        added_last_hour = (
            db.query(models.Company)
            .filter(models.Company.created_at >= hour_ago)
            .count()
        )
        with_site_last_hour = (
            db.query(models.Company)
            .filter(
                models.Company.created_at >= hour_ago,
                models.Company.website_url.isnot(None),
            )
            .count()
        )
        facts["companies"] = {
            "added_last_hour": added_last_hour,
            "added_with_website_last_hour": with_site_last_hour,
            "enrichment_rate_pct": round(with_site_last_hour / added_last_hour * 100, 1) if added_last_hour else None,
        }

        # Hunter ticks
        runs = (
            db.query(models.RunLog)
            .filter(models.RunLog.started_at >= hour_ago)
            .all()
        )
        import re
        hunter_seen = hunter_created = hunter_dup = hunter_err = 0
        for r in runs:
            d = r.details or ""
            m = re.search(r"seen=(\d+).*created=(\d+).*dup=(\d+).*errors=(\d+)", d)
            if m:
                hunter_seen += int(m.group(1))
                hunter_created += int(m.group(2))
                hunter_dup += int(m.group(3))
                hunter_err += int(m.group(4))
        facts["hunter"] = {
            "ticks": len(runs),
            "seen": hunter_seen,
            "created": hunter_created,
            "duplicates": hunter_dup,
            "errors": hunter_err,
        }

        # Outbox последний час по статусам
        outbox_recent = (
            db.query(models.OutboxMessage)
            .filter(models.OutboxMessage.created_at >= hour_ago)
            .all()
        )
        by_status: dict[str, int] = {}
        for m in outbox_recent:
            by_status[m.status or "unknown"] = by_status.get(m.status or "unknown", 0) + 1
        facts["outbox_last_hour"] = {
            "total": len(outbox_recent),
            "by_status": by_status,
        }

        # Agent runs — стоимость и прогон-успешность за час
        ar = (
            db.query(models.AgentRun)
            .filter(models.AgentRun.started_at >= hour_ago)
            .all()
        )
        sum_cost = sum(r.cost_usd or 0 for r in ar)
        sum_in = sum(r.input_tokens or 0 for r in ar)
        sum_cache = sum(r.cache_read_tokens or 0 for r in ar)
        successes = sum(1 for r in ar if r.success)
        facts["agent_runs_last_hour"] = {
            "total": len(ar),
            "successes": successes,
            "cost_usd": round(sum_cost, 5),
            "input_tokens": sum_in,
            "cache_read_tokens": sum_cache,
            "cache_hit_pct": round(sum_cache / (sum_in + sum_cache) * 100, 1) if (sum_in + sum_cache) else None,
        }

        # Предыдущий observation для контекста (delta)
        prev = (
            db.query(models.Observation)
            .order_by(models.Observation.id.desc())
            .first()
        )
        if prev:
            facts["previous_observation"] = {
                "created_at": prev.created_at.isoformat() + "Z" if prev.created_at else None,
                "summary": prev.summary,
            }
    return facts


def _summarize_with_deepseek(facts: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Вызов DeepSeek. Возвращает (summary_text, usage_dict). При 402 — naive."""
    api_key = settings.openrouter_api_key
    if not api_key:
        return _naive_summary(facts), {"input_tokens": 0, "output_tokens": 0, "cache_read": 0}

    from openai import OpenAI, APIStatusError
    import httpx

    http_kwargs: dict[str, Any] = {"timeout": 60.0}
    if settings.http_proxy_url:
        http_kwargs["proxy"] = settings.http_proxy_url
    client = OpenAI(
        api_key=api_key,
        base_url=settings.openrouter_base_url,
        http_client=httpx.Client(**http_kwargs),
    )
    user_msg = (
        "Snapshot за последний час:\n\n```json\n"
        + json.dumps(facts, ensure_ascii=False, indent=2, default=str)
        + "\n```\n\n2-4 предложения о том что изменилось и есть ли аномалии."
    )
    try:
        resp = client.chat.completions.create(
            model=COLLECTOR_MODEL,
            messages=[
                {"role": "system", "content": COLLECTOR_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=400,
            extra_headers={
                "HTTP-Referer": "https://lead-generator.ru",
                "X-Title": "Stenvik Observer",
            },
        )
    except APIStatusError as e:
        if e.status_code == 402:
            log.warning("collector: 402 from OpenRouter, fallback to naive summary")
            return _naive_summary(facts), {"input_tokens": 0, "output_tokens": 0, "cache_read": 0}
        raise

    text = (resp.choices[0].message.content or "").strip()
    usage = resp.usage
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details:
        cached = getattr(details, "cached_tokens", 0) or 0
    return text or _naive_summary(facts), {
        "input_tokens": (getattr(usage, "prompt_tokens", 0) or 0) - cached,
        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "cache_read": cached,
    }


def _naive_summary(facts: dict[str, Any]) -> str:
    """Fallback summary без LLM — формируем ручкой из facts."""
    c = facts.get("companies", {})
    h = facts.get("hunter", {})
    o = facts.get("outbox_last_hour", {})
    a = facts.get("agent_runs_last_hour", {})
    parts = []
    parts.append(f"Hunter: {h.get('ticks', 0)} ticks, {h.get('created', 0)} created, {h.get('duplicates', 0)} dup, {h.get('errors', 0)} errors.")
    parts.append(f"Companies +{c.get('added_last_hour', 0)} (with site: {c.get('added_with_website_last_hour', 0)}).")
    parts.append(f"Outbox last hour: {o.get('total', 0)} ({o.get('by_status', {})}).")
    parts.append(f"Agent_runs: {a.get('total', 0)} (success={a.get('successes', 0)}), cost ${a.get('cost_usd', 0)}, cache_hit {a.get('cache_hit_pct')}%.")
    return " ".join(parts)


def _detect_kind(summary: str, facts: dict[str, Any]) -> str:
    """Эвристика для kind = anomaly | hourly_snapshot."""
    if summary.upper().startswith("АНОМАЛИЯ"):
        return "anomaly"
    h = facts.get("hunter", {})
    if h.get("ticks", 0) > 0 and h.get("created", 0) == 0:
        return "anomaly"
    a = facts.get("agent_runs_last_hour", {})
    if a.get("total", 0) > 0 and (a.get("successes", 0) / a["total"]) < 0.5:
        return "anomaly"
    return "hourly_snapshot"


def collect_one_hour() -> dict[str, Any]:
    """Один проход hourly collector. Возвращает отчёт."""
    started = datetime.utcnow()
    facts = gather_hourly_facts()
    summary, usage = _summarize_with_deepseek(facts)
    cost_usd = estimate_cost_usd(COLLECTOR_MODEL, {
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cache_read_input_tokens": usage["cache_read"],
    })
    kind = _detect_kind(summary, facts)

    # Сохраняем agent_run + observation
    record = AgentRunRecord(
        agent_kind="collector",
        model=COLLECTOR_MODEL,
        iterations=1 if usage["input_tokens"] else 0,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_read_tokens=usage["cache_read"],
        cost_usd=cost_usd,
        success=True,
        summary=f"observation kind={kind}",
        started_at=started,
    )
    record.persist()

    with SessionLocal() as db:
        # Получаем agent_run.id который только что записался — берём последний по started_at
        ar = (
            db.query(models.AgentRun)
            .filter(models.AgentRun.agent_kind == "collector")
            .order_by(models.AgentRun.id.desc())
            .first()
        )
        obs = models.Observation(
            kind=kind,
            summary=summary[:2000],
            payload=facts,
            cost_usd=cost_usd,
            agent_run_id=ar.id if ar else None,
        )
        db.add(obs)
        db.commit()
        obs_id = obs.id

    log.info("collector: kind=%s cost=$%.5f obs_id=%d summary=%r",
             kind, cost_usd, obs_id, summary[:100])
    return {
        "obs_id": obs_id,
        "kind": kind,
        "summary": summary,
        "cost_usd": cost_usd,
        "facts": facts,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if len(sys.argv) > 1 and sys.argv[1] == "facts-only":
        print(json.dumps(gather_hourly_facts(), ensure_ascii=False, indent=2, default=str))
        return 0
    result = collect_one_hour()
    print("=" * 70)
    print(f"Observation #{result['obs_id']}  kind={result['kind']}  cost=${result['cost_usd']:.5f}")
    print("-" * 70)
    print(result["summary"])
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
