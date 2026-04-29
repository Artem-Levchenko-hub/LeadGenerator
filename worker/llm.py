"""Тонкая обёртка над Anthropic SDK с поддержкой:

- ANTHROPIC_BASE_URL для прокси (sk-hub-... ключи).
- Prompt caching (cache_control={'type': 'ephemeral'}) для общего префикса.
- Запись `agent_runs` с токенами и стоимостью.
- Tool use (ReAct-цикл) с автоматическим вызовом локальных функций-инструментов.

Используется всеми агентами (Outreach, Sales, Discovery, ...).
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Callable

from anthropic import Anthropic

from app.config import settings
from app.database import SessionLocal
from app import models


# === Цены моделей ($/MTok) — для подсчёта cost_usd. ===
# Источник: docs.anthropic.com/en/docs/about-claude/models. Цены могут меняться;
# при ошибке расчёта получаем приблизительное значение, не критично — для бюджета.
MODEL_PRICING = {
    # claude-sonnet-4-6: input $3 / output $15 / cache_read $0.30 / cache_write $3.75 per MTok
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),
    # claude-opus-4-7: input $15 / output $75 / cache_read $1.50 / cache_write $18.75
    "claude-opus-4-7": (15.0, 75.0, 1.50, 18.75),
    "claude-haiku-4-5-20251001": (0.80, 4.0, 0.08, 1.0),
}


def estimate_cost_usd(model: str, usage: dict) -> float:
    """Возвращает приблизительную стоимость одного вызова в USD."""
    in_p, out_p, cache_read_p, cache_write_p = MODEL_PRICING.get(
        model, (3.0, 15.0, 0.30, 3.75),  # дефолт = Sonnet
    )
    return (
        usage.get("input_tokens", 0) * in_p / 1_000_000
        + usage.get("output_tokens", 0) * out_p / 1_000_000
        + usage.get("cache_read_input_tokens", 0) * cache_read_p / 1_000_000
        + usage.get("cache_creation_input_tokens", 0) * cache_write_p / 1_000_000
    )


def get_anthropic_client() -> Anthropic:
    """Создаёт Anthropic-клиент.

    - Уважает ANTHROPIC_BASE_URL (custom endpoint прокси-провайдера).
    - Уважает HTTP_PROXY_URL (HTTPS/SOCKS5 прокси для исходящих) — нужно
      на VPS в РФ для доступа к api.anthropic.com.
    """
    import httpx

    kwargs: dict[str, Any] = {"api_key": settings.anthropic_api_key}
    if settings.anthropic_base_url:
        kwargs["base_url"] = settings.anthropic_base_url

    proxy = settings.http_proxy_url.strip()
    if proxy:
        # httpx >= 0.28 использует параметр `proxy` (singular).
        # Включаем поддержку SOCKS5 если в URL начинается с socks5://
        kwargs["http_client"] = httpx.Client(
            proxy=proxy, timeout=60.0, follow_redirects=True,
        )

    return Anthropic(**kwargs)


@dataclass
class AgentRunRecord:
    """Аккумулятор статистики одного запуска агента."""
    agent_kind: str
    task_id: int | None = None
    company_id: int | None = None
    model: str | None = None
    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    success: bool = False
    error_text: str | None = None
    summary: str | None = None
    started_at: datetime = field(default_factory=datetime.utcnow)

    def add_usage(self, model: str, usage: dict) -> None:
        self.model = model
        self.iterations += 1
        self.input_tokens += usage.get("input_tokens", 0) or 0
        self.output_tokens += usage.get("output_tokens", 0) or 0
        self.cache_read_tokens += usage.get("cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += usage.get("cache_creation_input_tokens", 0) or 0
        self.cost_usd += estimate_cost_usd(model, usage)

    def persist(self) -> None:
        """Записывает запуск в agent_runs."""
        with SessionLocal() as db:
            run = models.AgentRun(
                agent_kind=self.agent_kind,
                task_id=self.task_id,
                company_id=self.company_id,
                started_at=self.started_at,
                finished_at=datetime.utcnow(),
                model=self.model,
                iterations=self.iterations,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cache_read_tokens=self.cache_read_tokens,
                cache_write_tokens=self.cache_write_tokens,
                cost_usd=self.cost_usd,
                success=self.success,
                error_text=self.error_text,
                summary=self.summary,
            )
            db.add(run)
            db.commit()


def run_react_loop(
    agent_kind: str,
    system_blocks: list[dict],
    user_message: str,
    tools: list[dict],
    tool_handlers: dict[str, Callable[..., Any]],
    *,
    model: str | None = None,
    max_iterations: int | None = None,
    task_id: int | None = None,
    company_id: int | None = None,
) -> AgentRunRecord:
    """ReAct-цикл с tool use. Вызывает Anthropic API, исполняет tools локально,
    возвращает результаты модели до тех пор, пока модель не вызовет `finish`
    или не упрётся в max_iterations.

    `system_blocks` — список блоков system. Первый блок (общий префикс)
    помечается cache_control для prompt caching.

    `tools` — список JSON-схем tools (формат Anthropic tool use).
    `tool_handlers[tool_name]` — Python-функция, принимает kwargs из tool_input,
    возвращает строку (то, что увидит модель в tool_result).

    Управляющий tool `finish(summary)` ОБЯЗАТЕЛЕН в `tools` — модель должна
    закончить через него; иначе run помечается как `not_finished`.
    """
    model = model or settings.model_default
    max_iterations = max_iterations or settings.outreach_max_iterations
    record = AgentRunRecord(
        agent_kind=agent_kind, task_id=task_id, company_id=company_id, model=model,
    )
    client = get_anthropic_client()

    # Помечаем первый системный блок как cached prefix.
    system = []
    for i, block in enumerate(system_blocks):
        if i == 0 and len(block.get("text", "")) > 1024:
            system.append({**block, "cache_control": {"type": "ephemeral"}})
        else:
            system.append(block)

    messages: list[dict] = [{"role": "user", "content": user_message}]
    finished = False

    try:
        for _ in range(max_iterations):
            resp = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system,
                tools=tools,
                messages=messages,
            )
            usage = getattr(resp, "usage", None)
            if usage is not None:
                record.add_usage(model, {
                    "input_tokens": getattr(usage, "input_tokens", 0),
                    "output_tokens": getattr(usage, "output_tokens", 0),
                    "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
                    "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
                })

            # stop_reason 'end_turn' — модель закончила говорить (но без tool — ошибка).
            # 'tool_use' — нужно выполнить tool и вернуть результат.
            stop_reason = getattr(resp, "stop_reason", None)
            content_blocks = list(resp.content)

            if stop_reason == "end_turn" and not any(
                getattr(b, "type", None) == "tool_use" for b in content_blocks
            ):
                # Модель закончила без вызова finish — это ошибка цикла.
                record.summary = "ended without finish() tool call"
                break

            # Записываем reply модели в messages (assistant).
            messages.append({
                "role": "assistant",
                "content": [
                    {"type": "text", "text": b.text} if getattr(b, "type", None) == "text"
                    else {
                        "type": "tool_use",
                        "id": b.id,
                        "name": b.name,
                        "input": b.input,
                    }
                    for b in content_blocks
                ],
            })

            tool_results = []
            for b in content_blocks:
                if getattr(b, "type", None) != "tool_use":
                    continue
                tool_name = b.name
                tool_input = b.input or {}
                handler = tool_handlers.get(tool_name)
                if handler is None:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": f"ERROR: unknown tool '{tool_name}'",
                        "is_error": True,
                    })
                    continue
                try:
                    result = handler(**tool_input)
                except Exception as e:  # noqa: BLE001
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": f"ERROR: {type(e).__name__}: {e}",
                        "is_error": True,
                    })
                    continue
                # finish() возвращает специальный sentinel.
                if tool_name == "finish":
                    record.success = True
                    record.summary = (tool_input or {}).get("summary", "") or str(result or "")
                    finished = True
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": "OK",
                    })
                    break
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": str(result) if result is not None else "OK",
                })

            if finished:
                break

            messages.append({"role": "user", "content": tool_results})

        if not finished and not record.summary:
            record.summary = f"max_iterations ({max_iterations}) reached"

    except Exception as e:  # noqa: BLE001
        record.success = False
        record.error_text = f"{type(e).__name__}: {e}"

    finally:
        record.persist()

    return record
