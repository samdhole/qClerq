from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="session")
def test_settings_env(tmp_path_factory):
    env_file = tmp_path_factory.mktemp("env") / ".env.test"
    env_file.write_text(
        "APPROVAL_TIER_1_MAX=500.0\n"
        "APPROVAL_TIER_2_MAX=5000.0\n"
        "CONFIDENCE_THRESHOLD=0.70\n"
        "MANAGER_EMAIL=manager@test.local\n"
        "CFO_EMAIL=cfo@test.local\n"
        "SHEET_ID=test-sheet-id\n"
        "LLAMA_CLOUD_API_KEY=test-llama\n"
        "PDFCO_API_KEY=test-pdfco\n"
        "ANTHROPIC_API_KEY=test-anthropic\n"
        "QB_CLIENT_ID=test-qb-id\n"
        "QB_CLIENT_SECRET=test-qb-secret\n"
        "QB_REFRESH_TOKEN=test-refresh\n"
        "QB_REALM_ID=test-realm\n"
        "QB_DEFAULT_EXPENSE_ACCOUNT_ID=1\n"
        "JOBBER_ACCESS_TOKEN=test-jobber\n"
        "GOOGLE_SERVICE_ACCOUNT_JSON={}\n"
    )
    return str(env_file)


@pytest.fixture(scope="session")
def client(test_settings_env):
    from app.config import Settings, get_settings

    def override_settings():
        return Settings(_env_file=test_settings_env)

    app = create_app()
    app.dependency_overrides[get_settings] = override_settings
    with TestClient(app) as c:
        yield c
