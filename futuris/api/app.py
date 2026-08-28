"""Minimal FastAPI application instance and health endpoints."""

from typing import Any

from fastapi import FastAPI

from futuris import __version__
from futuris.infra.logging import configure_logging, get_logger

configure_logging()
logger = get_logger("futuris.api")

app = FastAPI(
    title="FUTURIS API",
    description="Predictive-intelligence and forecasting platform API",
    version=__version__,
)


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """Health check endpoint returning system status and current version."""
    logger.info("health_check_invoked", status="ok", version=__version__)
    return {
        "status": "ok",
        "version": __version__,
    }
