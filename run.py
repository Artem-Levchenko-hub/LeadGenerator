"""CLI для агент-режима: Claude Code /loop ищет компании через WebSearch,
анализирует, пишет в веб-приложение Stenvik Leads.

Режимы работы (автовыбор по .env):
  REMOTE (API): если в .env есть STENVIK_API_URL + STENVIK_API_TOKEN —
      лиды уходят в веб-приложение на VPS по HTTP.
  LOCAL:  иначе — пишутся в локальную SQLite + Яндекс.Диск (старый режим).

Команды:
    py run.py save-analysis         # JSON на stdin → лид в API или в локалку
    py run.py check-dup <url|name>  # exit 0 если уже в БД, 1 если новая
    py run.py stats                 # сводка (только LOCAL режим)
    py run.py recent [N]            # последние N (только LOCAL режим)
    py run.py rebuild-dashboard     # пересобрать Я.Диск дашборд (LOCAL)
    py run.py mode                  # показать текущий режим + настройки
"""
import json
import os
import sys

# Принудительно UTF-8 для stdin/stdout на Windows (иначе падает на Unicode)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except AttributeError:
    pass


def _api_config() -> tuple[str, str] | None:
    """Возвращает (url, token) если оба заданы, иначе None (→ LOCAL mode)."""
    # Сначала из env, потом из pydantic settings (которые читают .env)
    url = os.environ.get("STENVIK_API_URL", "").strip()
    tok = os.environ.get("STENVIK_API_TOKEN", "").strip()
    if not (url and tok):
        try:
            from app.config import settings
            url = url or (settings.stenvik_api_url or "").strip()
            tok = tok or (settings.stenvik_api_token or "").strip()
        except Exception:
            pass
    if url and tok:
        return url.rstrip("/"), tok
    return None


def _cmd_save_analysis(argv: list[str]) -> int:
    api = _api_config()
    if api:
        return _remote_save_analysis(*api)
    from pipeline.save_analyzed import main as save_main
    return save_main(argv)


def _remote_save_analysis(api_url: str, token: str) -> int:
    """Читает JSON с stdin, POST в /api/leads/import на VPS."""
    import httpx

    raw = sys.stdin.read().strip()
    if not raw:
        print("Error: no JSON on stdin", file=sys.stderr)
        return 2
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON: {e}", file=sys.stderr)
        return 2

    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{api_url}/api/leads/import",
                json=data,
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as e:
        print(f"Error: can't reach {api_url}: {e}", file=sys.stderr)
        return 1

    if r.status_code >= 500:
        print(f"Error: server {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return 1
    result = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
    if r.status_code == 401:
        print("Error: unauthorized (неверный STENVIK_API_TOKEN)", file=sys.stderr)
        return 1
    if r.status_code == 400:
        print(f"Error: bad data: {result.get('detail', r.text)}", file=sys.stderr)
        return 1
    if result.get("skipped"):
        print(f"[skip] duplicate: {data.get('company_name')} "
              f"(existing_id={result.get('existing_id')}, priority={result.get('priority')})")
        return 0
    if result.get("ok"):
        print(f"[ok] {result.get('company_name')} → id={result.get('id')}, "
              f"priority={result.get('priority')} (remote API)")
        return 0
    print(f"Unexpected response: {result}", file=sys.stderr)
    return 1


def _cmd_check_dup(argv: list[str]) -> int:
    if not argv:
        print("Usage: py run.py check-dup <url or company name>", file=sys.stderr)
        return 2

    # Построение dedup_key — одинаково для local и remote
    from pipeline.save_analyzed import _dedup_key, _normalize_url
    arg = argv[0].strip()
    if arg.startswith(("http://", "https://")) or "." in arg.split("/")[0]:
        key = f"url:{_normalize_url(arg)}"
    else:
        key = _dedup_key(arg, None)

    api = _api_config()
    if api:
        return _remote_check_dup(key, *api)

    # LOCAL mode
    from sqlalchemy import select
    from app.database import SessionLocal, init_db
    from app.models import ProcessedLead

    init_db()
    db = SessionLocal()
    try:
        existing = db.scalar(
            select(ProcessedLead).where(ProcessedLead.dedup_key == key)
        )
        if existing is not None:
            print(json.dumps({
                "duplicate": True,
                "id": existing.id,
                "company_name": existing.company_name,
                "sheet_row": existing.sheet_row,
                "priority": existing.priority,
            }, ensure_ascii=False))
            return 0
        print(json.dumps({"duplicate": False, "dedup_key": key}, ensure_ascii=False))
        return 1
    finally:
        db.close()


def _remote_check_dup(key: str, api_url: str, token: str) -> int:
    import httpx
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(
                f"{api_url}/api/leads/check-dup",
                params={"key": key},
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as e:
        print(f"Error: can't reach {api_url}: {e}", file=sys.stderr)
        return 2
    if r.status_code != 200:
        print(f"Error: server {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return 2
    result = r.json()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("duplicate") else 1


def _cmd_stats(argv: list[str]) -> int:
    from sqlalchemy import select, func
    from app.database import SessionLocal, init_db
    from app.models import ProcessedLead

    init_db()
    db = SessionLocal()
    try:
        total = db.scalar(select(func.count(ProcessedLead.id))) or 0
        by_priority = dict(
            db.execute(
                select(ProcessedLead.priority, func.count(ProcessedLead.id))
                .group_by(ProcessedLead.priority)
            ).all()
        )
        print(json.dumps({
            "total_processed": total,
            "by_priority": by_priority,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_recent(argv: list[str]) -> int:
    from sqlalchemy import select
    from app.database import SessionLocal, init_db
    from app.models import ProcessedLead

    init_db()
    limit = int(argv[0]) if argv else 20
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(ProcessedLead)
            .order_by(ProcessedLead.analyzed_at.desc())
            .limit(limit)
        ).all()
        print(json.dumps([
            {
                "id": r.id,
                "company_name": r.company_name,
                "website_url": r.website_url,
                "city": r.city,
                "priority": r.priority,
                "sheet_row": r.sheet_row,
                "analyzed_at": r.analyzed_at.isoformat() if r.analyzed_at else None,
            }
            for r in rows
        ], ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_loop_state(argv: list[str]) -> int:
    """Управление флагом состояния цикла.

        py run.py loop-state get          # вывести "running" | "stopped"
        py run.py loop-state set running  # включить цикл
        py run.py loop-state set stopped  # попросить агента остановиться на след. тике
    """
    from pathlib import Path
    state_file = Path("data/.loop_state")
    state_file.parent.mkdir(parents=True, exist_ok=True)

    if not argv:
        state = state_file.read_text().strip() if state_file.exists() else "stopped"
        print(state)
        return 0

    action = argv[0]
    if action == "get":
        state = state_file.read_text().strip() if state_file.exists() else "stopped"
        print(state)
        return 0
    elif action == "set":
        if len(argv) < 2 or argv[1] not in ("running", "stopped"):
            print("Usage: loop-state set <running|stopped>", file=sys.stderr)
            return 2
        state_file.write_text(argv[1], encoding="utf-8")
        print(argv[1])
        return 0
    else:
        print(f"Unknown loop-state action: {action}", file=sys.stderr)
        return 2


def _cmd_rebuild_dashboard(argv: list[str]) -> int:
    """Пересобирает HTML-дашборд из SQLite и заливает на Я.Диск.

    py run.py rebuild-dashboard
    """
    from sqlalchemy import select
    from app.database import SessionLocal, init_db
    from app.models import ProcessedLead
    from pipeline.yandex_sheet import regenerate_dashboard

    init_db()
    db = SessionLocal()
    try:
        rows = db.scalars(select(ProcessedLead)).all()
        leads = [
            {
                "company_name": r.company_name,
                "website_url": r.website_url,
                "phone": r.phone,
                "city": r.city,
                "industry": r.industry,
                "summary": r.summary,
                "recommended_services": r.recommended_services or [],
                "priority": r.priority,
                "md_public_url": r.md_public_url,
                "analyzed_at": r.analyzed_at.isoformat() if r.analyzed_at else None,
            }
            for r in rows
        ]
        url = regenerate_dashboard(leads)
        print(f"[ok] Dashboard rebuilt — {len(leads)} лидов → {url}")
        return 0
    finally:
        db.close()


def _cmd_mode(argv: list[str]) -> int:
    api = _api_config()
    if api:
        url, tok = api
        masked = tok[:6] + "…" + tok[-4:] if len(tok) > 12 else "***"
        print(f"Mode: REMOTE (API)")
        print(f"  URL:   {url}")
        print(f"  Token: {masked}")
        # Пинг /health
        try:
            import httpx
            with httpx.Client(timeout=10.0) as c:
                r = c.get(f"{url}/health")
            print(f"  Health: {r.status_code} {r.text[:100]}")
        except Exception as e:
            print(f"  Health: FAIL — {e}")
    else:
        print("Mode: LOCAL (SQLite + Яндекс.Диск)")
        print("  Чтобы переключиться на REMOTE, задай в .env:")
        print("    stenvik_api_url=http://<IP>:8080")
        print("    stenvik_api_token=<секрет>")
    return 0


COMMANDS = {
    "save-analysis": _cmd_save_analysis,
    "check-dup": _cmd_check_dup,
    "stats": _cmd_stats,
    "recent": _cmd_recent,
    "loop-state": _cmd_loop_state,
    "rebuild-dashboard": _cmd_rebuild_dashboard,
    "mode": _cmd_mode,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return 2
    return COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
