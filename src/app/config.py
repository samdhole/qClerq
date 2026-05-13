from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Claude model pinned to 4.6 for all LLM extraction and vendor matching
CLAUDE_MODEL = "claude-sonnet-4-6"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    approval_tier_1_max: float = 500.0
    approval_tier_2_max: float = 5000.0
    confidence_threshold: float = 0.70

    manager_email: str
    cfo_email: str
    sheet_id: str

    llama_cloud_api_key: str = ""
    pdfco_api_key: str = ""
    anthropic_api_key: str

    qb_client_id: str = ""
    qb_client_secret: str = ""
    qb_refresh_token: str = ""
    qb_realm_id: str = ""
    qb_default_expense_account_id: str = "1"

    jobber_access_token: str = ""

    google_service_account_json: str = "{}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
