"""Идемпотентная миграция SQLite-схемы: расширяем processed_leads и создаём
новые таблицы users / activity_logs для веб-приложения.

Запуск:
    .venv/Scripts/python.exe -m app.migrate

Безопасно для повторного запуска — каждая операция проверяется через PRAGMA.
Существующие 8 лидов сохраняются, новые колонки заполняются дефолтами.
"""
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings
from app.database import init_db


def _sqlite_path() -> Path:
    url = settings.database_url
    if not url.startswith("sqlite"):
        raise RuntimeError(f"Миграция поддерживает только sqlite, получено: {url}")
    # sqlite:///./data/leads.db → ./data/leads.db
    raw = url.split("///", 1)[-1]
    return Path(raw).resolve()


# === Новые колонки, которые нужно ADD COLUMN в processed_leads ===
NEW_COLUMNS_PROCESSED_LEADS = [
    ("website_status",   "VARCHAR(32)"),
    ("pains",            "JSON"),
    ("sales_hook",       "TEXT"),
    ("priority_reason",  "TEXT"),
    ("called",           "BOOLEAN DEFAULT 0 NOT NULL"),
    ("called_at",        "DATETIME"),
    ("called_by_id",     "INTEGER REFERENCES users(id)"),
    ("deal_status",      "VARCHAR(32) DEFAULT 'new' NOT NULL"),
    ("feedback",         "TEXT"),
    ("assigned_to_id",   "INTEGER REFERENCES users(id)"),
    # DEFAULT CURRENT_TIMESTAMP не работает для ALTER TABLE ADD COLUMN в SQLite
    # (non-constant default). Добавляем nullable, сразу после — UPDATE.
    ("updated_at",       "DATETIME"),
    # Апсейлы и скрипты для продажника
    ("upsell_offers",    "JSON"),
    ("talking_points",   "JSON"),
    ("objections",       "JSON"),
    # Страна лида (ISO-2: RU/US/KZ/BY/AE/...)
    ("country",          "VARCHAR(2) DEFAULT 'RU'"),
]


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def migrate() -> dict:
    """Выполняет миграцию. Возвращает отчёт о сделанных изменениях."""
    db_path = _sqlite_path()
    if not db_path.exists():
        print(f"[info] БД не существует, создам новую: {db_path}")
        init_db()  # Создаст всё с нуля через SQLAlchemy
        return {"created_from_scratch": True, "path": str(db_path)}

    report = {
        "path": str(db_path),
        "added_columns": [],
        "created_tables": [],
        "already_up_to_date": False,
    }

    # 1) Создаём недостающие таблицы (users, activity_logs) через SQLAlchemy.
    #    init_db вызывает Base.metadata.create_all() — он безопасно пропускает
    #    существующие таблицы и создаёт только новые.
    init_db()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # 2) ALTER TABLE processed_leads ADD COLUMN ... для новых полей
        if _table_exists(conn, "processed_leads"):
            existing = _existing_columns(conn, "processed_leads")
            for col_name, col_type in NEW_COLUMNS_PROCESSED_LEADS:
                if col_name in existing:
                    continue
                sql = f"ALTER TABLE processed_leads ADD COLUMN {col_name} {col_type}"
                print(f"[alter] {sql}")
                conn.execute(sql)
                report["added_columns"].append(col_name)
            conn.commit()

        # 3) Проверяем что новые таблицы появились
        for t in ("users", "activity_logs"):
            if _table_exists(conn, t):
                report["created_tables"].append(t)

        # 4) Индексы — SQLAlchemy create_all уже создал их, но на всякий случай
        #    можно добавить пропущенные. Пропускаем, доверяем create_all.

        # 5) Нормализация: заполним дефолты у существующих лидов
        conn.execute(
            "UPDATE processed_leads SET deal_status='new' WHERE deal_status IS NULL"
        )
        conn.execute(
            "UPDATE processed_leads SET called=0 WHERE called IS NULL"
        )
        # updated_at ставим равным analyzed_at для существующих записей
        conn.execute(
            "UPDATE processed_leads "
            "SET updated_at = analyzed_at WHERE updated_at IS NULL"
        )
        # Старые лиды по умолчанию RU
        conn.execute(
            "UPDATE processed_leads SET country='RU' WHERE country IS NULL OR country=''"
        )
        conn.commit()

        # 6) Итоги
        total_leads = conn.execute(
            "SELECT COUNT(*) FROM processed_leads"
        ).fetchone()[0]
        total_users = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        report["total_leads"] = total_leads
        report["total_users"] = total_users
        report["already_up_to_date"] = (
            not report["added_columns"]
        )

    finally:
        conn.close()

    return report


def migrate_v2_agent_studio() -> dict:
    """Миграция v2: создаёт новые таблицы агентной студии и сеет начальные данные.

    Идемпотентна. Базовая работа делается через init_db() (create_all безопасно
    создаёт только отсутствующие таблицы); потом сидим стартовые записи в
    kill_switch и cases.
    """
    db_path = _sqlite_path()
    if not db_path.exists():
        init_db()  # создаст всё с нуля если БД пустая
    else:
        init_db()  # добавит новые таблицы поверх старых
        # ALTER для уже существующих таблиц
        conn = sqlite3.connect(db_path)
        try:
            existing = _existing_columns(conn, "agent_runs")
            if existing and "trace" not in existing:
                conn.execute("ALTER TABLE agent_runs ADD COLUMN trace JSON")
                conn.commit()
        finally:
            conn.close()

    report = {
        "path": str(db_path),
        "seeded_kill_switch": False,
        "seeded_cases": [],
    }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # 1) Сеем kill_switch — единственная строка, состояние = running.
        if _table_exists(conn, "kill_switch"):
            row = conn.execute("SELECT id FROM kill_switch WHERE id=1").fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO kill_switch (id, state, reason, set_at) "
                    "VALUES (1, 'running', NULL, CURRENT_TIMESTAMP)"
                )
                report["seeded_kill_switch"] = True

        # 2) Сеем стартовые кейсы портфолио (kanavto, kamelia, innertalk).
        #    ВАЖНО: для innertalk явно прописываем restrictions_text про шифрование.
        if _table_exists(conn, "cases"):
            seed_cases = [
                {
                    "name": "kanavto.ru",
                    "url": "https://kanavto.ru",
                    "industry": "автосервис",
                    "services": '["корпоративный сайт","онлайн-бронирование","личный кабинет"]',
                    "summary": (
                        "Премиум-автосервис в Краснодаре для европейских авто (BMW, "
                        "Mercedes, Audi, Porsche). 4 филиала, 71k+ клиентов с 1995. "
                        "Сайт с онлайн-бронированием, личным кабинетом и историей работ."
                    ),
                    "metrics_text": "71 000+ клиентов · 4 филиала · премия 2ГИС",
                    "restrictions_text": None,
                },
                {
                    "name": "kamelia",
                    "url": "https://kamelia.innertalk.space",
                    "industry": "стоматология",
                    "services": '["корпоративный сайт","онлайн-запись","рассрочка"]',
                    "summary": (
                        "Сетевая стоматология в Казани. 5 филиалов, 42 специалиста, "
                        "4.9★ (1240 отзывов). Сайт с онлайн-записью, упоминаниями "
                        "3D-сканеров и ИИ-диагностики."
                    ),
                    "metrics_text": "5 филиалов · 42 специалиста · 4.9★ (1240 отзывов)",
                    "restrictions_text": None,
                },
                {
                    "name": "innertalk.space",
                    "url": "https://innertalk.space",
                    "industry": "корпоративные сервисы",
                    "services": '["веб-приложение","видеозвонки","корпоративный мессенджер"]',
                    "summary": (
                        "Корпоративный мессенджер с групповыми и видеозвонками. "
                        "Чаты, рабочие пространства, обмен файлами для команд."
                    ),
                    "metrics_text": None,
                    "restrictions_text": (
                        "🚫 НЕЛЬЗЯ упоминать шифрование/E2E/encryption в любом виде "
                        "при описании этого кейса. Описывай только функции "
                        "(групповые звонки, видеозвонки, мессенджер) без заявлений "
                        "о защищённости/приватности на уровне крипто."
                    ),
                },
            ]
            for case in seed_cases:
                exists = conn.execute(
                    "SELECT id FROM cases WHERE name=?", (case["name"],)
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO cases "
                    "(name, url, industry, services, summary, metrics_text, "
                    " restrictions_text, active, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)",
                    (
                        case["name"], case["url"], case["industry"],
                        case["services"], case["summary"], case["metrics_text"],
                        case["restrictions_text"],
                    ),
                )
                report["seeded_cases"].append(case["name"])

        conn.commit()
    finally:
        conn.close()

    return report


def main() -> int:
    try:
        report = migrate()
    except Exception as e:
        print(f"[error] Migration v1 failed: {e}", file=sys.stderr)
        return 1

    print("\n=== Migration v1 report ===")
    for k, v in report.items():
        print(f"  {k}: {v}")
    if report.get("already_up_to_date"):
        print("[ok] БД (v1) уже актуальная.")
    else:
        print("[ok] Миграция v1 применена.")

    # Migration v2: agent studio tables + seed.
    try:
        v2 = migrate_v2_agent_studio()
    except Exception as e:
        print(f"[error] Migration v2 failed: {e}", file=sys.stderr)
        return 1
    print("\n=== Migration v2 (agent studio) report ===")
    for k, v in v2.items():
        print(f"  {k}: {v}")
    print("========================\n")
    print("[ok] Миграция v2 применена.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
