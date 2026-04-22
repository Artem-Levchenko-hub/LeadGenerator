from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    anthropic_api_key: str
    app_secret: str = "dev-secret-change-me"
    database_url: str = "sqlite:///./data/leads.db"
    auth_users: str = "admin:admin123"

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
