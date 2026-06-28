import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes.health import router as health_router
from app.routes.chat import router as chat_router
from app.routes.ws import router as ws_router
from app.services.llm import warmup

logger = logging.getLogger(__name__)


async def wait_for_qdrant(url: str, retries: int = 10, delay: float = 2.0):
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{url}/healthz")
                if resp.status_code == 200:
                    return
        except httpx.ConnectError:
            pass
        await asyncio.sleep(delay)
    raise RuntimeError(f"Qdrant not reachable at {url} after {retries} attempts")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await wait_for_qdrant(settings.qdrant_url)
    logger.info("Warming up LLM provider=%s...", settings.llm_provider)
    await warmup()
    logger.info("LLM warm-up complete.")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(ws_router)
    return app


app = create_app()
