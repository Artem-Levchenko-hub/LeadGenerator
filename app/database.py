import os
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(db_url: str) -> None:
    """Для sqlite:///./data/leads.db создать папку ./data/ если её нет."""
    if not db_url.startswith("sqlite"):
        return
    # sqlite:///path/to/db или sqlite:////abs/path/db
    path_str = db_url.split("///", 1)[-1]
    if not path_str or path_str == ":memory:":
        return
    p = Path(path_str)
    p.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
