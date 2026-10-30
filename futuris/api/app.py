"""FastAPI application instance with OpenAPI contracts, error handlers, and UI static mount."""

import asyncio
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
from fastapi import Body, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
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
from futuris.api.routers.risk import router as risk_router
try:
    from futuris.predict.forecasting_engine import router as forecasting_engine_router
    from futuris.predict.realtime_event_impact import router as event_impact_router
    from futuris.predict.ensemble_reconciliation import router as ensemble_recon_router
    from futuris.predict.predictive_maintenance import router as predictive_maintenance_router
    from futuris.predict.market_regime_detector import router as market_regime_router
    from futuris.predict.event_probability_forecaster import router as event_prob_router
    from futuris.predict.scenario_stress_tester import router as stress_test_router
    from futuris.predict.prediction_calibration_optimizer import router as calibration_router
except ModuleNotFoundError:
    from predict.forecasting_engine import router as forecasting_engine_router
    from predict.realtime_event_impact import router as event_impact_router
    from predict.ensemble_reconciliation import router as ensemble_recon_router
    from predict.predictive_maintenance import router as predictive_maintenance_router
    from predict.market_regime_detector import router as market_regime_router
    from predict.event_probability_forecaster import router as event_prob_router
    from predict.scenario_stress_tester import router as stress_test_router
    from predict.prediction_calibration_optimizer import router as calibration_router
from futuris.api.routers.scenarios import fan_chart_router
from futuris.api.routers.scenarios import router as scenarios_router
from futuris.api.routers.self_status import router as self_status_router
from futuris.api.routers.streaming import router as streaming_router
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
_START_TIME: float = time.time()


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
    try:
        from futuris.infra.cpu import shutdown_cpu_process_pool

        await shutdown_cpu_process_pool()
    except Exception as exc:
        logger.error("cpu_process_pool_shutdown_failed", error=type(exc).__name__)
    await event_emitter.aclose()
    from futuris.storage.db import checkpoint_and_close_db

    await checkpoint_and_close_db()
    logger.info("application_shutdown")



_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Propagate a bounded, safe request ID; replace invalid client values."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        # Do not reflect unbounded or control-character-bearing client input into
        # response headers/log correlation. A generated UUID is safe and bounded.
        req_id = supplied if _REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid4())
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
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
app.include_router(fan_chart_router)
app.include_router(risk_router)
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
app.include_router(forecasting_engine_router, prefix="/api/v1/futuris")
app.include_router(event_impact_router, prefix="/api/v1/futuris")
app.include_router(ensemble_recon_router, prefix="/api/v1/futuris")
app.include_router(predictive_maintenance_router, prefix="/api/v1/futuris")
app.include_router(market_regime_router, prefix="/api/v1/futuris")
app.include_router(event_prob_router, prefix="/api/v1/futuris")
app.include_router(stress_test_router, prefix="/api/v1/futuris")
app.include_router(calibration_router, prefix="/api/v1/futuris")
app.include_router(predictions_router)
app.include_router(self_status_router)
app.include_router(streaming_router)
app.include_router(webhooks_router, prefix="/v1")
app.include_router(webhooks_router, prefix="/api/v1", include_in_schema=False)

# --- Ensemble Model Selector Integration ---
from predict.ensemble_model_selector import router as ensemble_selector_router
app.include_router(ensemble_selector_router, prefix="/api/v1/futuris")

# --- Forecast Explainability Engine Integration ---
from predict.forecast_explainability_engine import router as explainability_router
app.include_router(explainability_router, prefix="/api/v1/futuris")

# --- Prediction Drift Detector Integration ---
from predict.prediction_drift_detector import router as drift_detector_router
app.include_router(drift_detector_router, prefix="/api/v1/futuris")

# --- Skill Acquisition Planner Integration ---
from futuris.services.skill_acquisition_planner import router as skill_planner_router
app.include_router(skill_planner_router, prefix="/api/v1/futuris")

# --- Counterfactual Forecaster Integration ---
from futuris.services.counterfactual_forecaster import router as counterfactual_router
app.include_router(counterfactual_router, prefix="/api/v1/futuris")

# --- Quantum Forecast Engine Integration ---
from futuris.services.quantum_forecast_engine import router as quantum_forecast_router
app.include_router(quantum_forecast_router, prefix="/api/v1/futuris")

# --- Prediction Market Engine Integration ---
from futuris.services.prediction_market_engine import router as prediction_market_router
app.include_router(prediction_market_router, prefix="/api/v1/futuris")


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
    """Health check: process liveness and a measured storage verdict."""
    from futuris.storage.db import verify_schema

    storage = await verify_schema()
    return {
        "status": "ok" if storage["ready"] else "degraded",
        "service": "futuris",
        "evidence_class": "process_liveness",
        "observed_at": datetime.now(UTC).isoformat(),
        "version": __version__,
        "storage": storage,
    }


@app.head("/ready", include_in_schema=False)
@app.get("/ready", tags=["Health"])
async def readiness_check() -> JSONResponse:
    """Readiness probe: validates active database round-trip connectivity and schema readiness."""
    from sqlalchemy import text

    from futuris.storage.db import engine, verify_schema

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1;"))
        storage = await verify_schema()
        if storage.get("ready"):
            return JSONResponse(
                status_code=200,
                content={
                    "status": "ready",
                    "service": "futuris",
                    "database": "connected",
                    "database_backend": str(engine.dialect.name) if hasattr(engine, "dialect") else "unconfigured",
                    "version": __version__,
                    "observed_at": datetime.now(UTC).isoformat(),
                },
            )
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "service": "futuris",
                "database": "schema_incomplete",
                "database_backend": str(engine.dialect.name) if hasattr(engine, "dialect") else "unconfigured",
                "missing": storage.get("missing", []),
                "observed_at": datetime.now(UTC).isoformat(),
            },
        )
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "service": "futuris",
                "database": "disconnected",
                "database_backend": str(engine.dialect.name) if hasattr(engine, "dialect") else "unconfigured",
                "error": f"{type(exc).__name__}: {exc}",
                "observed_at": datetime.now(UTC).isoformat(),
            },
        )


@app.get("/status", tags=["Observability"])
async def service_status() -> dict[str, Any]:
    """Diagnostic operational status reporting telemetry, uptime, memory, and cache stats."""
    import os

    from futuris.infra.cache import forecast_cache
    from futuris.infra.resilience import peer_circuits

    uptime = round(time.time() - _START_TIME, 2)
    memory_info: dict[str, Any] = {}
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        memory_info = {
            "rss_mb": round(mem.rss / (1024 * 1024), 2),
            "vms_mb": round(mem.vms / (1024 * 1024), 2),
        }
    except Exception:
        memory_info = {"note": "process telemetry active"}

    return {
        "status": "healthy",
        "service": "futuris",
        "role": "Temporal Modeling, Scenario Simulation & Predictive Intelligence",
        "version": __version__,
        "environment": settings.APP_ENV,
        "port": 8000,
        "uptime_seconds": uptime,
        "observed_at": datetime.now(UTC).isoformat(),
        "memory": memory_info,
        "cache": forecast_cache.stats(),
        "circuit_breakers": peer_circuits.snapshots(),
        "scheduler_active": globals().get("_running_scheduler") is not None,
    }


@app.post("/forecast", tags=["Forecasting"])
@app.post("/v1/forecast", tags=["Forecasting"], include_in_schema=False)
async def quick_forecast(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Execute rapid forecasting pipeline for target metric and horizon with in-memory caching."""
    from futuris.api.routers.forecasts import parse_horizon
    from futuris.core.engine import ForecastEngine
    from futuris.infra.cache import forecast_cache
    from futuris.storage.db import async_session_factory
    from futuris.storage.repositories import ForecastRepository

    target = str(body.get("target") or "service:checkout:capacity_exceedance_24h")
    horizon = str(body.get("horizon") or "24h")
    cache_key = f"forecast:{target}:{horizon}"

    cached = forecast_cache.get(cache_key)
    if cached is not None:
        return cached

    h_delta = parse_horizon(horizon)
    engine = ForecastEngine()
    as_of = datetime.now(UTC)

    forecasts = await engine.orchestrate(target=target, as_of=as_of, horizon=h_delta)
    f = forecasts[0]
    async with async_session_factory() as session:
        f_repo = ForecastRepository(session)
        await f_repo.create(f)
        await session.commit()

    from futuris.core.enums import EvidenceClass

    ev_class = getattr(f, "evidence_class", None)
    if hasattr(ev_class, "value"):
        ev_val = ev_class.value
    else:
        ev_val = str(ev_class) if ev_class else "synthetic"
    is_live = ev_val == "live" or ev_class == EvidenceClass.LIVE

    result = {
        "forecast_id": str(f.forecast_id),
        "target": f.target,
        "prediction": round(float(f.prediction), 4),
        "range": {
            "lower": round(float(f.range_lower), 4),
            "central": round(float(f.prediction), 4),
            "upper": round(float(f.range_upper), 4),
        },
        "probability": f.probability,
        "confidence": f.confidence.value,
        "model": f.model_version,
        "as_of": f.as_of.isoformat(),
        "horizon": horizon,
        "status": f.status.value,
        "evidence_class": ev_val,
        "evidence_source": getattr(f, "evidence_source", None),
        "input_telemetry_verified": is_live,
        "prediction_is_not_authorization": True,
        "executable_commands": [],
    }
    forecast_cache.set(cache_key, result, ttl_seconds=30.0)
    return result



@app.post("/simulate", tags=["Scenarios"])
@app.post("/v1/simulate", tags=["Scenarios"], include_in_schema=False)
async def quick_simulate(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Execute counterfactual scenario simulation against a baseline forecast."""
    from datetime import timedelta
    from uuid import UUID

    from futuris.core.engine import ForecastEngine
    from futuris.core.enums import ScenarioType
    from futuris.scenarios.engine import ScenarioEngine
    from futuris.scenarios.spec import ScenarioSpec
    from futuris.storage.db import async_session_factory
    from futuris.storage.repositories import ForecastRepository, ScenarioRepository

    forecast_id_raw = body.get("forecast_id") or body.get("base_forecast_id")
    async with async_session_factory() as session:
        f_repo = ForecastRepository(session)
        s_repo = ScenarioRepository(session)
        base = None
        if forecast_id_raw:
            try:
                base = await f_repo.get(UUID(str(forecast_id_raw)))
            except Exception:
                base = None
        if not base:
            recent = await f_repo.list_recent(limit=1)
            base = recent[0] if recent else None
        if not base:
            engine = ForecastEngine()
            forecasts = await engine.orchestrate(
                target=str(body.get("target") or "service:checkout:capacity_exceedance_24h"),
                as_of=datetime.now(UTC),
                horizon=timedelta(hours=24),
            )
            base = forecasts[0]
            await f_repo.create(base)
            await session.commit()

        name = str(body.get("name") or "Operational Stress Simulation")
        overrides = body.get("assumption_overrides") or {
            "demand_multiplier": 1.4,
            "capacity_multiplier": 0.8,
        }
        spec = ScenarioSpec(
            spec_id=uuid4(),
            name=name,
            scenario_type=ScenarioType.STRESS
            if body.get("is_stress", True)
            else ScenarioType.USER_DEFINED,
            assumption_overrides=overrides,
            rationale=str(body.get("rationale") or "Counterfactual simulation request"),
        )
        scenario_engine = ScenarioEngine(scenario_repo=s_repo)
        res = await scenario_engine.run_scenario(base_forecast=base, spec=spec)
        return {
            "scenario_id": str(res.spec.spec_id),
            "base_forecast_id": str(base.forecast_id),
            "name": res.spec.name,
            "perturbed_prediction": round(float(res.perturbed_prediction), 4),
            "perturbed_probability": res.perturbed_probability,
            "divergence": res.divergence,
            "perturbed_values": res.perturbed_values,
            "prediction_is_not_authorization": True,
            "executable_commands": [],
        }


@app.get("/trends", tags=["Trends"])
@app.get("/v1/trends", tags=["Trends"], include_in_schema=False)
async def quick_trends(limit: int = 10) -> dict[str, Any]:
    """Retrieve recent temporal trends and forecast trajectory history with in-memory cache."""
    cache_key = f"trends:limit_{limit}"
    from futuris.infra.cache import forecast_cache

    cached = forecast_cache.get(cache_key)
    if cached is not None:
        return cached

    from futuris.storage.db import async_session_factory
    from futuris.storage.repositories import ForecastRepository

    async with async_session_factory() as session:
        f_repo = ForecastRepository(session)
        recent = await f_repo.list_recent(limit=limit)
        trends = [
            {
                "forecast_id": str(f.forecast_id),
                "target": f.target,
                "prediction": round(float(f.prediction), 4),
                "range_lower": round(float(f.range_lower), 4),
                "range_upper": round(float(f.range_upper), 4),
                "probability": f.probability,
                "confidence": f.confidence.value,
                "created_at": f.created_at.isoformat() if f.created_at else None,
                "status": f.status.value,
            }
            for f in recent
        ]
        result = {
            "status": "ok",
            "count": len(trends),
            "trends": trends,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        forecast_cache.set(cache_key, result, ttl_seconds=15.0)
        return result


