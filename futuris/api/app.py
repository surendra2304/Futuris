"""FastAPI application instance with OpenAPI contracts, error handlers, and UI static mount."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import Scope

if TYPE_CHECKING:

    pass

from futuris import __version__
from futuris.api.errors import register_error_handlers
from futuris.api.routers.audit import router as audit_router
from futuris.api.routers.ecosystem import router as ecosystem_router
from futuris.api.routers.evaluation import router as evaluation_router
from futuris.api.routers.events import router as events_router
from futuris.api.routers.forecasts import router as forecasts_router
from futuris.api.routers.friday import router as friday_router
from futuris.api.routers.market import router as market_router
from futuris.api.routers.models import router as models_router
from futuris.api.routers.predictions import router as predictions_router
from futuris.api.routers.scenarios import router as scenarios_router
from futuris.api.routers.self_status import router as self_status_router
from futuris.api.routers.webhooks import router as webhooks_router
from futuris.demo.seed import DemoSeeder
from futuris.demo.startup_policy import should_seed_demo_on_startup
from futuris.infra.config import settings
from futuris.infra.events import event_emitter
from futuris.infra.logging import configure_logging, get_logger
from futuris.infra.metrics import metrics_endpoint
from futuris.storage.db import async_session_factory, ensure_schema
from futuris.storage.models import ForecastModel
from futuris.upgrade.safe_config import is_production_environment

configure_logging()
logger = get_logger("futuris.api")

# Published by the lifespan so /v1/self/status reports the scheduler that is
# actually running in this process rather than assuming it
# (SelfHealingSupervisor.check_scheduler reads this attribute).
_running_scheduler: Any = None


def iter_route_paths(application: FastAPI) -> set[str]:
    """Every route path mounted on the app, including router-mounted routes.

    Current FastAPI wraps ``include_router`` results in a lazy ``_IncludedRouter``
    that exposes no ``.path`` attribute, so a naive ``getattr(route, "path", ...)``
    walk silently drops every router-mounted route and reports the agent surface
    as broken when it is not. This walker expands included routers through their
    effective candidates and is duck-typed so it survives FastAPI upgrades.
    """
    paths: set[str] = set()

    def walk(route: Any) -> None:
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            for candidate in candidates():
                walk(candidate)
            return
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)

    for route in application.routes:
        walk(route)
    return paths


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize storage and background workers without fabricating production telemetry."""
    import sys

    try:
        report = await ensure_schema()
        if report["ready"]:
            logger.info("startup_db_tables_verified", tables=report["present"])
        else:
            # Booting with an unusable schema must be loud: requests will be
            # refused with a storage error until it is fixed.
            logger.error(
                "startup_db_schema_incomplete",
                missing=report["missing"],
                error=report["error"],
            )
    except Exception as exc:
        logger.error("startup_init_failed", error=f"{type(exc).__name__}: {exc}")

    # Outbound webhook deliveries share one pooled HTTP client for the process
    # lifetime instead of opening a connection per event.
    if event_emitter._client is None:
        event_emitter._client = httpx.AsyncClient(
            timeout=5.0, limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
        )

    # The unattended agent loop must actually run: without it nothing refreshes
    # forecasts, sweeps lifecycles or backtests unless a human calls the API.
    scheduler = None
    scheduler_session = None
    if "pytest" not in sys.modules and settings.SCHEDULER_ENABLED:
        from futuris.infra.scheduler import ForecastScheduler
        from futuris.storage.repositories import (
            EventRepository,
            ForecastRepository,
            OutcomeRepository,
        )

        scheduler_session = async_session_factory()
        scheduler = ForecastScheduler(
            forecast_repo=ForecastRepository(scheduler_session),
            outcome_repo=OutcomeRepository(scheduler_session),
            event_repo=EventRepository(scheduler_session),
            emitter=event_emitter,
        )
        scheduler.start()
        # Published at module level for /v1/self/status: the supervisor reports
        # what is actually running rather than assuming the scheduler started.
        # (A plain local assignment would be invisible to the module.)
        globals()["_running_scheduler"] = scheduler

    background_tasks: list[asyncio.Task] = []
    if "pytest" not in sys.modules and settings.SELF_HEALING_ENABLED:
        from futuris.infra.self_healing import self_healing_loop

        background_tasks.append(
            asyncio.create_task(self_healing_loop(settings.SELF_HEALING_INTERVAL_SECONDS))
        )
    if "pytest" not in sys.modules:
        from futuris.integrations.memora_event_consumer import memora_event_worker

        if settings.MEMORA_API_KEY:
            background_tasks.append(asyncio.create_task(memora_event_worker()))

        if should_seed_demo_on_startup(settings.APP_ENV, settings.STARTUP_DEMO_SEED_ENABLED):

            async def _bg_seed():
                try:
                    await asyncio.sleep(2.0)
                    async with async_session_factory() as session:
                        count_res = await session.execute(
                            select(func.count(ForecastModel.forecast_id))
                        )
                        forecast_count = count_res.scalar_one_or_none() or 0

                    if forecast_count == 0:
                        logger.info("startup_db_empty_initiating_opt_in_demo_seed")
                        seeder = DemoSeeder(seed=42)
                        await seeder.run(days=7, backtest_days=0, fast_mode=True)
                        logger.info("startup_demo_seed_completed")
                except Exception as exc:
                    logger.warning("startup_background_seed_failed", error=str(exc))

            background_tasks.append(asyncio.create_task(_bg_seed()))

    yield
    if scheduler is not None:
        await scheduler.shutdown()
    globals()["_running_scheduler"] = None
    if scheduler_session is not None:
        await scheduler_session.close()
    for task in background_tasks:
        task.cancel()
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)
    await event_emitter.aclose()
    logger.info("application_shutdown")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Middleware ensuring every request has and echoes an X-Request-ID header."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        req_id = request.headers.get("X-Request-ID", str(uuid4()))
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


# Policy for the single-page console only. The console is one same-origin module
# script with no inline script and no external stylesheet or font (verified against
# futuris/ui/dist/index.html). 'unsafe-inline' is limited to styles because React and
# recharts set style attributes at runtime. Swagger UI on /docs loads its assets from a
# CDN and would be broken by this policy, which is why it is scoped to /ui.
UI_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline browser hardening headers (S12).

    ``nosniff``, ``X-Frame-Options`` and the referrer and permissions policies apply
    to every response. The CSP applies only to ``/ui``. HSTS is sent only in
    production, because over plain HTTP in development it would pin the browser to
    an origin that does not serve TLS.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        path = request.url.path
        if path == "/ui" or path.startswith("/ui/"):
            headers.setdefault("Content-Security-Policy", UI_CONTENT_SECURITY_POLICY)
        if is_production_environment(settings.APP_ENV):
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def _docs_enabled() -> bool:
    """OpenAPI and Swagger UI: on outside production unless DOCS_ENABLED says otherwise (S11)."""
    if settings.DOCS_ENABLED is not None:
        return settings.DOCS_ENABLED
    return not is_production_environment(settings.APP_ENV)


_DOCS_ENABLED = _docs_enabled()

app = FastAPI(
    title="FUTURIS API",
    description=(
        "Production-grade standalone predictive-intelligence and forecasting API. "
        "Provides calibration, evidence anchoring, scenarios, and decision support."
    ),
    version=__version__,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
    lifespan=lifespan,
)

# 1. Register Middlewares
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# 2. Register Global Error Envelope Handlers
register_error_handlers(app)

# 3. Mount Versioned Routers (/v1)
# Each router is mounted once under its canonical prefix. The /api and alias mounts
# are kept because existing callers use them, but they are hidden from the OpenAPI
# schema so one operation is documented once (M12). Canonical: /v1/futuris, /v1/market
# is an alias of the same handler, /v1/webhooks paths live under /v1.
app.include_router(forecasts_router)
app.include_router(scenarios_router)
app.include_router(evaluation_router)
app.include_router(events_router)
app.include_router(models_router)
app.include_router(audit_router)
app.include_router(friday_router)
app.include_router(friday_router, prefix="/api", include_in_schema=False)
app.include_router(ecosystem_router)
app.include_router(market_router, prefix="/v1/futuris")
app.include_router(market_router, prefix="/api/v1/futuris", include_in_schema=False)
app.include_router(market_router, prefix="/v1/market", include_in_schema=False)
app.include_router(predictions_router)
app.include_router(self_status_router)
app.include_router(webhooks_router, prefix="/v1")
app.include_router(webhooks_router, prefix="/api/v1", include_in_schema=False)


@app.post("/v1/task/execute", tags=["Universal Task Protocol"])
@app.post("/api/v1/task/execute", tags=["Universal Task Protocol"], include_in_schema=False)
async def execute_task(body: dict):
    """Reject generic tasks until they can be routed to a real evidence-backed handler."""
    from fastapi import HTTPException, status

    action = body.get("action", "forecast")
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else body

    # Invariant: PREDICTION IS NOT AUTHORIZATION
    # Strictly reject command executions, mitigations, or website changes
    action_lower = str(action).lower().strip()
    forbidden = {
        "execute",
        "mitigate",
        "apply_mitigation",
        "website_change",
        "scale",
        "scale_up",
        "run_command",
        "bash",
        "deploy",
        "exec",
        "command",
    }
    if action_lower in forbidden or any(
        k in payload
        for k in ["command", "commands", "script", "bash_command", "exec", "mitigation_command"]
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Prediction is not authorization: Futuris does not execute mitigations, "
                "system commands, or website changes. It only provides calibrated forecasts."
            ),
        )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "The generic task endpoint has no evidence-backed handler configured. "
            "No forecast was run and no result was produced. Use the authenticated "
            "forecast API with timestamped telemetry."
        ),
    )




class SPAStaticFiles(StaticFiles):
    """StaticFiles that falls back to index.html for Single Page Applications (SPA)."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
            if response.status_code == 404 and not path.startswith("assets/"):
                return await super().get_response("index.html", scope)
            return response
        except (HTTPException, Exception):
            if not path.startswith("assets/"):
                return await super().get_response("index.html", scope)
            raise


# 4. Mount Production UI Build Output if available
ui_dist_path = Path(__file__).parent.parent / "ui" / "dist"
if ui_dist_path.exists():
    app.mount("/ui", SPAStaticFiles(directory=str(ui_dist_path), html=True), name="ui")


@app.get("/metrics", tags=["Observability"])
async def get_metrics() -> Response:
    """Prometheus metrics scrape endpoint."""
    return metrics_endpoint()


@app.head("/", include_in_schema=False)
@app.get("/", tags=["Root"])
async def root(request: Request) -> Any:
    """Root endpoint for UptimeRobot / uptime probes. Redirects browsers to UI."""
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(url="/ui/")
    return {
        "status": "ok",
        "service": "FUTURIS",
        "version": __version__,
    }


@app.head("/health", include_in_schema=False)
@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, Any]:
    """Health check: process liveness *and* a measured storage verdict.

    This answer proves the process is up and serving requests. It does not probe
    any dependency, so it says so instead of leaving the reader to guess -- but
    storage is checked, because a process that is up while its database is
    unusable is not healthy. ``status`` is ``degraded`` when storage is not
    ready, and the storage block carries the measurement.
    """
    from futuris.storage.db import verify_schema

    storage = await verify_schema()
    return {
        "status": "ok" if storage["ready"] else "degraded",
        "evidence_class": "process_liveness_plus_storage_probe",
        "observed_at": datetime.now(UTC).isoformat(),
        "version": __version__,
        "storage": storage,
    }
