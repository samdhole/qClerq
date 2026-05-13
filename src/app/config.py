from __future__ import annotations

import json
import logging
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Claude model pinned to 4.6 for all LLM extraction and vendor matching
CLAUDE_MODEL = "claude-sonnet-4-6"

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    approval_tier_1_max: float = 500.0
    approval_tier_2_max: float = 5000.0
    confidence_threshold: float = 0.70
    max_upload_bytes: int = 20_971_520  # 20 MB

    manager_email: str
    cfo_email: str
    sheet_id: str
    api_key: str

    llama_cloud_api_key: str = ""
    pdfco_api_key: str = ""
    anthropic_api_key: str

    qb_client_id: str = ""
    qb_client_secret: str = ""
    qb_refresh_token: str = ""
    qb_realm_id: str = ""
    qb_default_expense_account_id: str = "1"

    jobber_access_token: str = ""

    google_service_account_json: str
    valid_approvers: list[str] = []

    @field_validator("google_service_account_json")
    @classmethod
    def _validate_service_account(cls, v: str) -> str:
        try:
            data = json.loads(v)
        except json.JSONDecodeError as e:
            raise ValueError(f"google_service_account_json is not valid JSON: {e}") from e
        for key in ("client_email", "private_key"):
            if key not in data:
                raise ValueError(f"google_service_account_json missing required key: '{key}'")
        return v

    @model_validator(mode="after")
    def _warn_qb_account_id(self) -> "Settings":
        qb_configured = any([self.qb_client_id, self.qb_client_secret, self.qb_refresh_token])
        if qb_configured and self.qb_default_expense_account_id == "1":
            logger.warning(
                "qb_default_expense_account_id is '1' but QB credentials are configured — "
                "set QB_DEFAULT_EXPENSE_ACCOUNT_ID to the correct account ID."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
