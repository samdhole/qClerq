from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # L-2: validate configuration eagerly at boot. A missing/invalid required env var
    # (api_key, gemini_api_key, sheet_id, google_service_account_json, ...) fails loudly
    # here instead of surfacing as a 500 on the first request that touches settings.
    from app.config import get_settings

    get_settings()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="AI Invoice Desk", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
