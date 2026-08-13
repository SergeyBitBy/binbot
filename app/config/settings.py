from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

class Settings(BaseSettings):
    bot_token: str = "7777777777:AAEE_placeholder_token_change_me"
    initial_admin_username: str = "sergebybitp2p"
    initial_allowed_chat_id: int = 930460307
    timezone: str = "Europe/Kyiv"
    log_level: str = "INFO"
    
    database_url: str = f"sqlite+aiosqlite:///{DATA_DIR / 'bot.db'}"
    
    google_sheets_enabled: bool = False
    google_service_account_file: str = "data/google_credentials.json"
    google_spreadsheet_id: str = ""
    
    # Binance Provider defaults
    binance_request_timeout: float = 15.0
    binance_max_retries: int = 3
    binance_rate_limit_delay: float = 1.0  # seconds between requests
    binance_max_pages: int = 50
    binance_detail_concurrency: int = 5
    binance_detail_refresh_minutes: int = 30
    monitoring_dispatch_interval_seconds: int = 5
    monitoring_max_concurrency: int = 3
    monitoring_lease_seconds: int = 300
    notification_max_attempts: int = 10
    notification_worker_interval_seconds: int = 5
    log_retention_days: int = 30
    backup_retention_count: int = 14
    
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
