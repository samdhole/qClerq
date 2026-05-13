from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="AI Invoice Desk", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
