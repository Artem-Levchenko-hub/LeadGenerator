from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Anthropic API — не используется, если анализ делает агент Claude Code через /loop.
    # Оставлен для обратной совместимости (например, если захочешь вернуть API-режим).
    anthropic_api_key: str = ""

    # Yandex Disk — куда сливаем готовые лиды в xlsx
    yandex_disk_token: str = ""
    yandex_disk_file_path: str = "/Stenvik/leads.xlsx"

    app_secret: str = "dev-secret-change-me"
    database_url: str = "sqlite:///./data/leads.db"
    auth_users: str = "admin:admin123"

    # Токен для машинного импорта лидов (Bearer в Authorization-заголовке)
    ingest_token: str = ""

    # Для локального run.py: если задан, save-analysis и check-dup ходят
    # в удалённое API вместо локальной БД.
    stenvik_api_url: str = ""
    stenvik_api_token: str = ""

    hh_cities: str = "1,2,3,4,38,113"
    hh_exclude_industries: str = "7"
    pipeline_interval_minutes: int = 15
    leads_per_run: int = 10
    claude_model: str = "claude-sonnet-4-6"

    tz: str = "Europe/Moscow"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cities_list(self) -> list[int]:
        return [int(c.strip()) for c in self.hh_cities.split(",") if c.strip()]

    @property
    def excluded_industries_list(self) -> list[str]:
        return [s.strip() for s in self.hh_exclude_industries.split(",") if s.strip()]

    @property
    def auth_users_dict(self) -> dict[str, str]:
        result = {}
        for pair in self.auth_users.split(","):
            if ":" in pair:
                u, p = pair.split(":", 1)
                result[u.strip()] = p.strip()
        return result


settings = Settings()
