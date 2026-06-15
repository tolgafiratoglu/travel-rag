import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.config import settings
from app.routes.health import router as health_router


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
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
