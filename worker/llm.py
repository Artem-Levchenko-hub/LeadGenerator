"""LLM-обёртка для агентов: мульти-провайдер с tool use ReAct loop.

Поддерживаемые провайдеры (см. settings.effective_provider):
- "openrouter" — OpenRouter (https://openrouter.ai). Один ключ → любая модель:
  Claude (deepseek/...), DeepSeek, GPT, Gemini, Qwen и т.д. OpenAI-формат API.
  ⭐ Главный путь для РФ (можно оплатить картой РФ через bothub/proxyapi/etc.).
- "anthropic" — нативный Anthropic SDK или Anthropic-совместимый прокси
  (aihubmix.com и др.). Используется когда есть Anthropic-нативный ключ.
- "openai" — любой OpenAI-совместимый эндпоинт (vsegpt.ru и т.п.).

Tools у нас определены в Anthropic-format (`name`, `input_schema`). При запросе
к OpenAI-совместимому API мы конвертируем их на лету в OpenAI tool-calls и
обратно — модель не видит разницы.

Caching:
- Anthropic native: явный cache_control={'type':'ephemeral'} → 90% экономии.
- OpenRouter/DeepSeek: автоматический prefix caching (одинаковый префикс →
  кэшируется). Просто отправляем общий префикс первым.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Callable

from app.config import settings
from app.database import SessionLocal
from app import models


log = logging.getLogger(__name__)


# === Цены $/1M tokens (input, output, cache_read, cache_write) ===
# Источник: docs провайдеров. Используется для записи cost_usd в agent_runs.
MODEL_PRICING = {
    # Anthropic native + reseller (Sonnet/Haiku/Opus)
    "claude-sonnet-4-6":               (3.0, 15.0, 0.30, 3.75),
    "claude-opus-4-7":                 (15.0, 75.0, 1.50, 18.75),
    "claude-haiku-4-5":                (0.80, 4.0, 0.08, 1.0),
    "claude-haiku-4-5-20251001":       (0.80, 4.0, 0.08, 1.0),
    # DeepSeek
    # OpenRouter via DeepInfra (самый дешёвый роут на 2026-04): input ~$0.32/M,
    # output ~$0.89/M. Был указан $0.14/$0.28 (наверное VseGPT) — неточно для OR.
    "deepseek/deepseek-chat":          (0.32, 0.89, 0.032, 0.032),
    "deepseek/deepseek-chat-v3":       (0.32, 0.89, 0.032, 0.032),
    "deepseek/deepseek-chat-v3.1":     (0.32, 0.89, 0.032, 0.032),
    "deepseek/deepseek-v3.1":          (0.32, 0.89, 0.032, 0.032),
    "deepseek-chat":                   (0.32, 0.89, 0.032, 0.032),
    # GPT
    "openai/gpt-5":                    (1.25, 10.0, 0.125, 0.125),
    "openai/gpt-5-mini":               (0.25, 2.0, 0.025, 0.025),
    "gpt-5":                           (1.25, 10.0, 0.125, 0.125),
    "gpt-5-mini":                      (0.25, 2.0, 0.025, 0.025),
    # Anthropic via OpenRouter (используется CEO/Strategic Orchestrator).
    "anthropic/claude-opus-4.6":       (5.0, 25.0, 0.50, 6.25),
    "anthropic/claude-opus-4.7":       (5.0, 25.0, 0.50, 6.25),
    "anthropic/claude-sonnet-4-6":     (3.0, 15.0, 0.30, 3.75),
    "anthropic/claude-haiku-4-5":      (0.80, 4.0, 0.08, 1.0),
}


def estimate_cost_usd(model: str, usage: dict) -> float:
    in_p, out_p, cache_read_p, cache_write_p = MODEL_PRICING.get(
        model, (3.0, 15.0, 0.30, 3.75),
    )
    return (
        usage.get("input_tokens", 0) * in_p / 1_000_000
        + usage.get("output_tokens", 0) * out_p / 1_000_000
        + usage.get("cache_read_input_tokens", 0) * cache_read_p / 1_000_000
        + usage.get("cache_creation_input_tokens", 0) * cache_write_p / 1_000_000
    )


# === Конвертация Anthropic tool format → OpenAI tool format ===

def anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Anthropic: {name, description, input_schema}
    OpenAI:    {type: "function", function: {name, description, parameters}}
    """
    out = []
    for t in tools:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return out


@dataclass
class AgentRunRecord:
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
    trace: list = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.utcnow)

    def add_usage(self, model: str, usage: dict) -> None:
        self.model = model
        self.iterations += 1
        self.input_tokens += int(usage.get("input_tokens", 0) or 0)
        self.output_tokens += int(usage.get("output_tokens", 0) or 0)
        self.cache_read_tokens += int(usage.get("cache_read_input_tokens", 0) or 0)
        self.cache_write_tokens += int(usage.get("cache_creation_input_tokens", 0) or 0)
        self.cost_usd += estimate_cost_usd(model, usage)

    def add_trace_step(self, **fields: Any) -> None:
        """Добавляет шаг в trace. Используется для видимости работы агента на UI.

        Типичные поля: kind (text|tool_call|tool_result), tool, input_short,
        output_short, dur_ms.
        """
        step = {"step": len(self.trace) + 1, **fields}
        self.trace.append(step)

    def persist(self) -> None:
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
                trace=self.trace or None,
            )
            db.add(run)
            db.commit()


# === HTTP-клиент с прокси ===

def _httpx_client(*, use_proxy: bool = True):
    """Создаёт httpx-клиент. Прокси используется только когда use_proxy=True
    (для api.anthropic.com / api.openrouter.ai из РФ). Для российских прокси
    типа vsegpt.ru / proxyapi.ru / bothub.chat прокси не нужен и даже вреден.
    """
    import httpx
    kwargs: dict[str, Any] = {"timeout": 90.0}
    if use_proxy and settings.http_proxy_url:
        kwargs["proxy"] = settings.http_proxy_url
    return httpx.Client(**kwargs)


def _is_ru_endpoint(base_url: str) -> bool:
    """Определяет является ли base_url российским провайдером (без прокси)."""
    if not base_url:
        return False
    ru_hosts = ("vsegpt.ru", "proxyapi.ru", "bothub.chat", "gpthub.ru", "neuroapi.ru")
    return any(h in base_url.lower() for h in ru_hosts)


# === Anthropic backend (нативный) ===

def _run_anthropic(
    record: AgentRunRecord,
    system_blocks: list[dict],
    user_message: str,
    tools: list[dict],
    tool_handlers: dict[str, Callable[..., Any]],
    model: str,
    max_iterations: int,
) -> bool:
    """ReAct loop через Anthropic SDK. Возвращает finished?"""
    from anthropic import Anthropic

    kwargs: dict[str, Any] = {"api_key": settings.anthropic_api_key}
    if settings.anthropic_base_url:
        kwargs["base_url"] = settings.anthropic_base_url
    if settings.http_proxy_url:
        kwargs["http_client"] = _httpx_client()
    client = Anthropic(**kwargs)

    system = []
    for i, block in enumerate(system_blocks):
        if i == 0 and len(block.get("text", "")) > 1024:
            system.append({**block, "cache_control": {"type": "ephemeral"}})
        else:
            system.append(block)

    messages: list[dict] = [{"role": "user", "content": user_message}]

    for _ in range(max_iterations):
        resp = client.messages.create(
            model=model, max_tokens=4096,
            system=system, tools=tools, messages=messages,
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            record.add_usage(model, {
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
            })

        content_blocks = list(resp.content)
        stop_reason = getattr(resp, "stop_reason", None)

        if stop_reason == "end_turn" and not any(
            getattr(b, "type", None) == "tool_use" for b in content_blocks
        ):
            record.summary = "ended without finish() tool call"
            return True

        messages.append({
            "role": "assistant",
            "content": [
                {"type": "text", "text": b.text} if getattr(b, "type", None) == "text"
                else {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
                for b in content_blocks
            ],
        })

        tool_results = []
        finished = False
        for b in content_blocks:
            if getattr(b, "type", None) != "tool_use":
                continue
            handler = tool_handlers.get(b.name)
            if handler is None:
                tool_results.append({
                    "type": "tool_result", "tool_use_id": b.id,
                    "content": f"ERROR: unknown tool '{b.name}'", "is_error": True,
                })
                continue
            try:
                result = handler(**(b.input or {}))
            except Exception as e:  # noqa: BLE001
                tool_results.append({
                    "type": "tool_result", "tool_use_id": b.id,
                    "content": f"ERROR: {type(e).__name__}: {e}", "is_error": True,
                })
                continue
            if b.name == "finish":
                record.success = True
                record.summary = (b.input or {}).get("summary", "") or str(result or "")
                tool_results.append({"type": "tool_result", "tool_use_id": b.id, "content": "OK"})
                finished = True
                break
            tool_results.append({
                "type": "tool_result", "tool_use_id": b.id,
                "content": str(result) if result is not None else "OK",
            })

        if finished:
            return True
        messages.append({"role": "user", "content": tool_results})

    record.summary = record.summary or f"max_iterations ({max_iterations}) reached"
    return False


# === OpenAI/OpenRouter backend ===

def _system_blocks_to_str(system_blocks: list[dict]) -> str:
    """OpenAI принимает один system message — склеиваем все блоки."""
    return "\n\n".join(b.get("text", "") for b in system_blocks if b.get("text"))


def _run_openai(
    record: AgentRunRecord,
    system_blocks: list[dict],
    user_message: str,
    tools: list[dict],
    tool_handlers: dict[str, Callable[..., Any]],
    model: str,
    max_iterations: int,
) -> bool:
    """ReAct loop через OpenAI-совместимый API (OpenRouter / vsegpt / etc.)."""
    from openai import OpenAI

    api_key = settings.openrouter_api_key or settings.anthropic_api_key
    base_url = settings.openrouter_base_url
    kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url}
    use_proxy = settings.http_proxy_url and not _is_ru_endpoint(base_url)
    if use_proxy:
        kwargs["http_client"] = _httpx_client(use_proxy=True)
    else:
        kwargs["http_client"] = _httpx_client(use_proxy=False)
    client = OpenAI(**kwargs)

    openai_tools = anthropic_tools_to_openai(tools)
    system_text = _system_blocks_to_str(system_blocks)

    messages: list[dict] = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_message},
    ]

    extra_headers = {}
    if "openrouter" in (base_url or "").lower():
        # Реквизиты для OpenRouter rankings (не критично, но рекомендуется).
        extra_headers["HTTP-Referer"] = "https://lead-generator.ru"
        extra_headers["X-Title"] = "Stenvik Agent Studio"

    for _ in range(max_iterations):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=openai_tools,
                max_tokens=4096,
                extra_headers=extra_headers or None,
            )
        except Exception as e:  # noqa: BLE001
            record.error_text = f"{type(e).__name__}: {e}"
            return False

        usage = getattr(resp, "usage", None)
        if usage is not None:
            # OpenAI: prompt_tokens, completion_tokens; cache в .prompt_tokens_details.cached_tokens (если есть)
            cached = 0
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
            record.add_usage(model, {
                "input_tokens": (getattr(usage, "prompt_tokens", 0) or 0) - cached,
                "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "cache_read_input_tokens": cached,
            })

        choice = resp.choices[0]
        msg = choice.message
        finish_reason = choice.finish_reason

        # Trace: что сказала модель текстом
        if msg.content:
            record.add_trace_step(
                kind="thought", text=(msg.content or "")[:500],
            )

        # Записываем assistant message в историю.
        assistant_msg: dict = {"role": "assistant"}
        if msg.content:
            assistant_msg["content"] = msg.content
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        else:
            assistant_msg.setdefault("content", "")
        messages.append(assistant_msg)

        # Если модель завершила без tool_call'ов
        if finish_reason in ("stop", "length") and not msg.tool_calls:
            record.summary = (msg.content or "").strip()[:300] or "ended without finish() tool call"
            return True

        # Исполняем tool calls
        finished = False
        for tc in (msg.tool_calls or []):
            tool_name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments or "{}")
            except Exception:  # noqa: BLE001
                tool_args = {}
            t0 = datetime.utcnow()
            handler = tool_handlers.get(tool_name)
            if handler is None:
                content = f"ERROR: unknown tool '{tool_name}'"
            else:
                try:
                    result = handler(**tool_args)
                    content = str(result) if result is not None else "OK"
                except Exception as e:  # noqa: BLE001
                    content = f"ERROR: {type(e).__name__}: {e}"
            dur_ms = int((datetime.utcnow() - t0).total_seconds() * 1000)

            # Trace: вызов инструмента
            record.add_trace_step(
                kind="tool_call", tool=tool_name,
                input_short=json.dumps(tool_args, ensure_ascii=False)[:400],
                output_short=str(content)[:600],
                dur_ms=dur_ms,
            )

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": content,
            })

            if tool_name == "finish":
                record.success = True
                record.summary = tool_args.get("summary", "") or "finished"
                finished = True
                break

        if finished:
            return True

    record.summary = record.summary or f"max_iterations ({max_iterations}) reached"
    return False


# === Публичный API ===

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
    """ReAct-цикл с tool use. Авто-выбирает backend по settings.effective_provider.

    `tools` — Anthropic-format ({name, description, input_schema}). Конвертация
    в OpenAI-format происходит автоматически если backend = openai/openrouter.
    """
    model = model or settings.model_default
    max_iterations = max_iterations or settings.outreach_max_iterations
    record = AgentRunRecord(
        agent_kind=agent_kind, task_id=task_id, company_id=company_id, model=model,
    )

    provider = settings.effective_provider
    try:
        if provider == "anthropic":
            _run_anthropic(record, system_blocks, user_message, tools,
                           tool_handlers, model, max_iterations)
        elif provider in ("openrouter", "openai"):
            _run_openai(record, system_blocks, user_message, tools,
                        tool_handlers, model, max_iterations)
        else:
            record.error_text = (
                f"no LLM provider configured (provider={provider!r}). "
                "Set ANTHROPIC_API_KEY or OPENROUTER_API_KEY in .env."
            )
    except Exception as e:  # noqa: BLE001
        record.success = False
        record.error_text = f"{type(e).__name__}: {e}"
        log.exception("agent run failed (%s)", agent_kind)
    finally:
        record.persist()

    return record
