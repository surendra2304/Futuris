"""FastAPI application instance with OpenAPI contracts, error handlers, and UI static mount."""

from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from futuris import __version__
from futuris.api.errors import register_error_handlers
from futuris.api.routers.audit import router as audit_router
from futuris.api.routers.evaluation import router as evaluation_router
from futuris.api.routers.events import router as events_router
from futuris.api.routers.forecasts import router as forecasts_router
from futuris.api.routers.friday import router as friday_router
from futuris.api.routers.models import router as models_router
from futuris.api.routers.scenarios import router as scenarios_router
from futuris.infra.logging import configure_logging, get_logger
from futuris.infra.metrics import metrics_endpoint

configure_logging()
logger = get_logger("futuris.api")


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

# 4. Mount Production UI Build Output if available
ui_dist_path = Path(__file__).parent.parent / "ui" / "dist"
if ui_dist_path.exists():
    app.mount("/ui", StaticFiles(directory=str(ui_dist_path), html=True), name="ui")


@app.get("/metrics", tags=["Observability"])
async def get_metrics() -> Response:
    """Prometheus metrics scrape endpoint."""
    return metrics_endpoint()


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, Any]:
    """Health check endpoint returning system status and current version."""
    return {
        "status": "ok",
        "version": __version__,
    }
