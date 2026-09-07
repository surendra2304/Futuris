"""FastAPI application instance with OpenAPI contracts, error handlers, and UI static mount."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from starlette.middleware.base import BaseHTTPMiddleware

from futuris import __version__
from futuris.api.errors import register_error_handlers
from futuris.api.routers.audit import router as audit_router
from futuris.api.routers.ecosystem import router as ecosystem_router
from futuris.api.routers.evaluation import router as evaluation_router
from futuris.api.routers.events import router as events_router
from futuris.api.routers.forecasts import router as forecasts_router
from futuris.api.routers.friday import router as friday_router
from futuris.api.routers.models import router as models_router
from futuris.api.routers.scenarios import router as scenarios_router
from futuris.demo.seed import DemoSeeder
from futuris.infra.logging import configure_logging, get_logger
from futuris.infra.metrics import metrics_endpoint
from futuris.storage.db import async_session_factory, engine
from futuris.storage.models import Base, ForecastModel

configure_logging()
logger = get_logger("futuris.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: initialize database tables and auto-seed in background if empty."""
    import sys

    # Skip auto-creation and seeding during pytest runs to avoid fixture race conditions
    if "pytest" not in sys.modules:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("startup_db_tables_verified")

            async def _bg_seed():
                try:
                    await asyncio.sleep(1.0)
                    async with async_session_factory() as session:
                        count_res = await session.execute(select(func.count(ForecastModel.forecast_id)))
                        forecast_count = count_res.scalar_one_or_none() or 0

                    if forecast_count == 0:
                        logger.info("startup_db_empty_initiating_seed")
                        seeder = DemoSeeder(seed=42)
                        await seeder.run()
                        logger.info("startup_db_initial_seed_completed")
                except Exception as exc:
                    logger.warning("startup_background_seed_failed", error=str(exc))

            asyncio.create_task(_bg_seed())
        except Exception as exc:
            logger.warning("startup_init_failed", error=str(exc))

    yield
    logger.info("application_shutdown")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Middleware ensuring every request has and echoes an X-Request-ID header."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        req_id = request.headers.get("X-Request-ID", str(uuid4()))
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


app = FastAPI(
    title="FUTURIS API",
    description=(
        "Production-grade standalone predictive-intelligence and forecasting API. "
        "Provides calibration, evidence anchoring, scenarios, and decision support."
    ),
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# 1. Register Middlewares
app.add_middleware(RequestIdMiddleware)

# 2. Register Global Error Envelope Handlers
register_error_handlers(app)

# 3. Mount Versioned Routers (/v1)
app.include_router(forecasts_router)
app.include_router(scenarios_router)
app.include_router(evaluation_router)
app.include_router(events_router)
app.include_router(models_router)
app.include_router(audit_router)
app.include_router(friday_router)
app.include_router(ecosystem_router)

# 4. Mount Production UI Build Output if available
ui_dist_path = Path(__file__).parent.parent / "ui" / "dist"
if ui_dist_path.exists():
    app.mount("/ui", StaticFiles(directory=str(ui_dist_path), html=True), name="ui")


@app.get("/metrics", tags=["Observability"])
async def get_metrics() -> Response:
    """Prometheus metrics scrape endpoint."""
    return metrics_endpoint()


@app.api_route("/", methods=["GET", "HEAD"], tags=["Root"])
async def root() -> dict[str, Any]:
    """Root endpoint for UptimeRobot / uptime probes."""
    return {
        "status": "ok",
        "service": "FUTURIS",
        "version": __version__,
    }


@app.api_route("/health", methods=["GET", "HEAD"], tags=["Health"])
async def health_check() -> dict[str, Any]:
    """Health check endpoint returning system status and current version."""
    return {
        "status": "ok",
        "version": __version__,
    }
