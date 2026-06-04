from __future__ import annotations

import json
import logging
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GEMINI_MODEL = "gemini-3.1-flash-lite"

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

    gemini_model: str = GEMINI_MODEL

    gemini_api_key: str

    qb_client_id: str = ""
    qb_client_secret: str = ""
    qb_refresh_token: str = ""
    qb_realm_id: str = ""
    qb_default_expense_account_id: str = "1"
    qb_environment: str = "sandbox"

    # Jobber: the refresh token is the durable credential (access tokens live ~60 min and
    # are refreshed transparently by jobber_auth). jobber_access_token is legacy/seed-only.
    jobber_client_id: str = ""
    jobber_client_secret: str = ""
    jobber_refresh_token: str = ""
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
