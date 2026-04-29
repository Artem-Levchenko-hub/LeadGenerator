from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # === LLM-провайдер для агентов ===
    # llm_provider:
    #   "anthropic"   — нативный Anthropic API (или Anthropic-совместимый прокси).
    #   "openrouter"  — OpenRouter (OpenAI-формат, любая модель: deepseek, claude, gpt).
    #   "openai"      — OpenAI-совместимый API (vsegpt.ru, proxyapi.ru/openai, ...).
    # Auto-detect: если задан openrouter_api_key — берём openrouter; иначе anthropic.
    llm_provider: str = ""

    # Anthropic-нативный путь.
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""

    # OpenRouter / OpenAI-совместимый путь.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Отдельный ключ для CEO/Strategic Orchestrator (Opus 4.6, daily audit).
    # Если не задан — fallback на openrouter_api_key. Разделение нужно чтобы:
    # (a) бюджет CEO не съедал бюджет outreach, (b) видеть в дашборде OpenRouter
    # сколько именно стратегических аудитов было запущено.
    ceo_openrouter_api_key: str = ""
    ceo_model: str = "anthropic/claude-opus-4.7"

    # HTTP(S)/SOCKS5 прокси для исходящих (Anthropic + OpenRouter).
    http_proxy_url: str = ""

    # Модели по ролям. Менять можно без правки кода.
    # default — для Hunter analysis, Outreach, Sales, Discovery, Auditor.
    # premium — для Estimation, Proposal, Strategic.
    model_default: str = "claude-sonnet-4-6"
    model_premium: str = "claude-opus-4-7"
    daily_llm_budget_usd: float = 20.0

    @property
    def effective_provider(self) -> str:
        """Возвращает реально используемого провайдера (с авто-детектом)."""
        if self.llm_provider:
            return self.llm_provider.lower()
        if self.openrouter_api_key:
            return "openrouter"
        if self.anthropic_api_key:
            return "anthropic"
        return ""

    # === Yandex Disk (LOCAL-режим, fallback) ===
    yandex_disk_token: str = ""
    yandex_disk_file_path: str = "/Stenvik/leads.xlsx"

    app_secret: str = "dev-secret-change-me"
    database_url: str = "sqlite:///./data/leads.db"
    auth_users: str = "admin:admin123"

    # Токен для машинного импорта лидов (Bearer в Authorization).
    ingest_token: str = ""

    # REMOTE-режим run.py
    stenvik_api_url: str = ""
    stenvik_api_token: str = ""

    # === Hunter — источники лидов ===
    hh_cities: str = "1,2,3,4,38,113"
    hh_exclude_industries: str = "7"
    pipeline_interval_minutes: int = 15
    leads_per_run: int = 10
    claude_model: str = "claude-sonnet-4-6"  # legacy, не использовать в новом коде

    # 2GIS Catalog API — главный источник холодных лидов.
    twogis_api_key: str = ""
    twogis_cities: str = "Москва,Санкт-Петербург,Краснодар,Казань,Новосибирск,Екатеринбург,Ростов-на-Дону,Самара,Уфа,Челябинск"
    twogis_categories: str = "стоматология,автосервис,юристы,бухгалтерия,строительство,красота,клиника"

    # === Email (UniSender SMTP+IMAP) ===
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "outreach@stenvik.studio"
    smtp_from_name: str = "Stenvik"
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""

    # === Telegram bot ===
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_bot_username: str = "stenvik_studio_bot"

    # === SMS via smsc.ru ===
    smsc_login: str = ""
    smsc_password: str = ""

    # === Voice via Zvonok.com ===
    zvonok_api_key: str = ""
    zvonok_campaign_id: str = ""

    # === Дневные лимиты исходящих (safety rails) ===
    daily_email_limit: int = 30
    daily_telegram_limit: int = 100
    daily_sms_limit: int = 50
    daily_call_limit: int = 0
    outbox_holding_seconds: int = 600  # 10 минут
    conversation_loop_guard_msgs: int = 5
    outreach_max_iterations: int = 30

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

    @property
    def twogis_cities_list(self) -> list[str]:
        return [c.strip() for c in self.twogis_cities.split(",") if c.strip()]

    @property
    def twogis_categories_list(self) -> list[str]:
        return [c.strip() for c in self.twogis_categories.split(",") if c.strip()]


settings = Settings()
