"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import chat, export, extract, skills
from app.core.config import settings
from app.skills.registry import registry, watcher

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    logger.info("Loading skills from: %s", settings.skills_dir)
    registry.load_from_dir(settings.skills_dir)
    watcher.start(settings.skills_dir, registry)
    logger.info("Loaded %d skill(s)", len(registry.get_all()))
    yield
    # Shutdown
    watcher.stop()
    logger.info("Stopped skill watcher")


app = FastAPI(
    title="Skills Platform API",
    description="Market Research AI — Skills-as-Markdown",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(skills.router, prefix="/api/skills", tags=["skills"])
app.include_router(extract.router, prefix="/api", tags=["extract"])
app.include_router(export.router, prefix="/api", tags=["export"])


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "skills_loaded": len(registry.get_all()),
    }
