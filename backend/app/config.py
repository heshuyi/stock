from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "stock_advisor"
    config_path: str = str(
        Path(__file__).resolve().parents[2] / "configs" / "symbols.json"
    )
    market_db_path: str = str(
        Path(__file__).resolve().parents[1] / "data" / "market.db"
    )
    use_mock_data: bool = False
    sync_interval_seconds: int = 3600
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
