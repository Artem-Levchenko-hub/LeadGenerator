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


def main() -> int:
    try:
        report = migrate()
    except Exception as e:
        print(f"[error] Migration failed: {e}", file=sys.stderr)
        return 1

    print("\n=== Migration report ===")
    for k, v in report.items():
        print(f"  {k}: {v}")
    print("========================\n")
    if report.get("already_up_to_date"):
        print("[ok] БД уже актуальная, изменений не требуется.")
    else:
        print("[ok] Миграция успешно применена.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
