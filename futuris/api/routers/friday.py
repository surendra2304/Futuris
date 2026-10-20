import asyncio
import hmac
import math
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.api.deps import get_db_session, get_event_repo, get_outcome_repo
from futuris.core.enums import (
    EvidenceClass,
    ForecastEventType,
    ForecastStatus,
    ResolutionMethod,
    ScenarioType,
)
from futuris.core.pipeline import ForecastingPipeline
from futuris.core.schemas import Driver, ForecastEvent, Outcome
from futuris.evaluation.calibration import CalibrationAnalyzer, update_calibration_metrics
from futuris.features.normalize import (
    DataStalenessError,
    InsufficientDataError,
    check_data_staleness,
    check_insufficient_data,
)
from futuris.infra.audit import AuditLogger
from futuris.infra.auth import AuthUser
from futuris.infra.config import settings
from futuris.infra.logging import get_logger
from futuris.infra.research_context import fetch_research_reports, finding_labels
from futuris.scenarios.engine import ScenarioEngine
from futuris.scenarios.spec import ScenarioSpec
from futuris.storage.models import ForecastModel
from futuris.storage.repositories import (
    EventRepository,
    ForecastRepository,
    OutcomeRepository,
    ScenarioRepository,
)
from futuris.upgrade.rate_limit import InMemoryRateLimitBackend

router = APIRouter(prefix="/v1/friday", tags=["FRIDAY Delegation"])

logger = get_logger("futuris.api.friday")

#: Minimum accepted credential length for the delegation guard.
MIN_API_KEY_CHARS = 32

friday_limiter = InMemoryRateLimitBackend()


async def verify_friday_auth(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> AuthUser:
    """Verify incoming FRIDAY API Key against FUTURIS_FRIDAY_API_KEY config."""
    # Settings is the single source of truth for credentials (B21). Reading the
    # process environment here as well let a stray variable silently override the
    # configured key. The FRIDAY_API_KEY alias is resolved by Settings itself.
    #
    # A deployment that has not issued a dedicated FRIDAY key still has its own
    # master credential. Accepting that master key keeps delegation working for
    # the operator while remaining fail-closed: with no credential configured
    # at all the route refuses to serve.
    admin_key = settings.FUTURIS_API_KEY
    expected_key = settings.FUTURIS_FRIDAY_API_KEY or admin_key
    auth_key = x_api_key
    if not auth_key and authorization:
        if authorization.startswith("Bearer "):
            auth_key = authorization.replace("Bearer ", "").strip()
        else:
            auth_key = authorization.strip()

    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "FRIDAY service authentication is not configured. Set "
                "FUTURIS_FRIDAY_API_KEY or FUTURIS_API_KEY."
            ),
        )
    if len(expected_key) < MIN_API_KEY_CHARS:
        # A configured-but-short key silently disabled delegation and then
        # reported itself as "not configured", which sent operators looking for
        # the wrong problem.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"The configured FRIDAY credential is {len(expected_key)} characters; "
                f"at least {MIN_API_KEY_CHARS} are required. Generate one with "
                "`python -m futuris.cli create-admin-key` and set "
                "FUTURIS_FRIDAY_API_KEY to a high-entropy value."
            ),
        )
    is_valid = bool(auth_key) and (
        hmac.compare_digest(auth_key, expected_key)
        or bool(admin_key and hmac.compare_digest(auth_key, admin_key))
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing FRIDAY authentication credentials.",
        )

    decision = await friday_limiter.consume(f"friday:{auth_key}", limit=100, window_seconds=3600.0)
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"FRIDAY rate limit exceeded. Retry after {decision.retry_after_seconds:.1f}s.",
        )

    return AuthUser(
        label="friday_agent",
        role="analyst",
        principal_id="principal_friday",
        tenant_id="tenant_friday",
    )


class DriverItem(BaseModel):
    metric: str
    correlation: float
    lead_time: str


class PredictionPayload(BaseModel):
    point_estimate: float
    lower_bound: float
    upper_bound: float
    probability_distribution: dict[str, float]


class FridayForecastRequest(BaseModel):
    friday_request_id: str = Field(default_factory=lambda: f"req_{uuid4().hex[:8]}")
    target: str = Field(
        ...,
        description="Target metric (e.g. service:checkout:capacity_exceedance_24h)",
    )
    horizon: Literal["1h", "24h", "7d", "30d"] = "24h"
    confidence_level: Literal[0.80, 0.90, 0.95] = 0.90
    context: dict[str, Any] = Field(default_factory=dict)
    priority: Literal["normal", "urgent"] = "normal"
    idempotency_key: str | None = None
    telemetry_data: list[dict[str, Any]] | None = None
    allow_stale: bool = False


def _honest_calibration(forecast: Any) -> tuple[float, dict[str, Any]]:
    """Calibration score/metrics taken from measured data, or declared unmeasured.

    The stored ``calibration_metrics`` are produced by the calibration analyzer
    from resolved outcomes. When a target has never been resolved there is
    nothing measured to report, and the response says so instead of showing a
    plausible-looking constant.
    """
    metrics = dict(getattr(forecast, "calibration_metrics", None) or {})
    raw_count = metrics.get("n_resolved_outcomes", metrics.get("sample_count", 0))
    try:
        resolved = int(raw_count or 0)
    except (TypeError, ValueError):
        resolved = 0
    ece = metrics.get("ece", metrics.get("ece_score"))
    has_ece = isinstance(ece, (int, float)) and not isinstance(ece, bool)

    if resolved <= 0 or not has_ece:
        # Nothing was measured for this target. Report only what is actually
        # computable and say plainly that calibration is unmeasured.
        uncalibrated: dict[str, Any] = {
            "n_resolved_outcomes": resolved,
            "calibration_status": "uncalibrated",
        }
        if isinstance(metrics.get("brier_score"), (int, float)):
            uncalibrated["brier_score"] = metrics["brier_score"]
        return 0.0, uncalibrated

    return float(ece), {
        **{k: v for k, v in metrics.items() if not isinstance(v, dict)},
        "calibration_status": "measured",
    }


class FridayForecastResponse(BaseModel):
    futuris_forecast_id: UUID
    friday_request_id: str
    prediction: PredictionPayload
    confidence: str
    status: str = "COMPLETED"
    calibration_score: float
    calibration_metrics: dict[str, Any] = Field(default_factory=dict)
    predictive_distribution: dict[str, float] = Field(default_factory=dict)
    intervals: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)
    model_metadata: dict[str, Any] = Field(default_factory=dict)
    prediction_is_not_authorization: bool = True
    executable_commands: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    evidence_snapshot_id: str
    model_used: str
    drivers_identified: list[DriverItem]
    evidence_class: EvidenceClass = EvidenceClass.SYNTHETIC
    evidence_source: str | None = None

    @field_validator("prediction_is_not_authorization")
    @classmethod
    def validate_not_auth(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Invariant violation: prediction_is_not_authorization must be True")
        return True

    @field_validator("executable_commands")
    @classmethod
    def validate_no_commands(cls, v: list[str]) -> list[str]:
        if v:
            raise ValueError(
                "Invariant violation: Forecast responses must not contain executable commands"
            )
        return v


class FridayTaskEnvelope(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task_{uuid4().hex[:8]}")
    source_agent: str = "friday"
    target_agent: str = "futuris"
    action: str = "forecast"
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: str = "normal"
    idempotency_key: str | None = None


class FridayTaskEnvelopeResponse(BaseModel):
    task_id: str
    source_agent: str = "futuris"
    target_agent: str = "friday"
    status: str = "SUCCESS"
    action: str
    result: dict[str, Any] = Field(default_factory=dict)
    summary: str
    prediction_is_not_authorization: bool = True
    executable_commands: list[str] = Field(default_factory=list)
    execution_time_ms: int = 0

    @field_validator("prediction_is_not_authorization")
    @classmethod
    def validate_not_auth(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Invariant violation: prediction_is_not_authorization must be True")
        return True

    @field_validator("executable_commands")
    @classmethod
    def validate_no_commands(cls, v: list[str]) -> list[str]:
        if v:
            raise ValueError(
                "Invariant violation: Forecast responses must not contain executable commands"
            )
        return v


class VariableChange(BaseModel):
    metric: str
    change_pct: float


class FridayScenarioSpec(BaseModel):
    name: str = "counterfactual_scenario"
    variable_changes: list[VariableChange]
    assumptions: list[str] = Field(default_factory=list)
    scenario_type: str = "stress"
    time_horizon: str = "24h"
    confidence_level: float = 0.90


class FridayScenarioRequest(BaseModel):
    question: str
    base_forecast_id: UUID
    scenario_spec: FridayScenarioSpec


class FridayScenarioResponse(BaseModel):
    scenario_id: UUID
    divergent_prediction: float
    probability_outcome: float
    risk_assessment: str
    comparison_to_baseline: dict[str, Any]
    assumptions: list[str] = Field(default_factory=list)
    scenario_spec: dict[str, Any] = Field(default_factory=dict)
    prediction_is_not_authorization: bool = True
    executable_commands: list[str] = Field(default_factory=list)

    @field_validator("prediction_is_not_authorization")
    @classmethod
    def validate_not_auth(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Invariant violation: prediction_is_not_authorization must be True")
        return True

    @field_validator("executable_commands")
    @classmethod
    def validate_no_commands(cls, v: list[str]) -> list[str]:
        if v:
            raise ValueError(
                "Invariant violation: Scenario responses must not contain executable commands"
            )
        return v


class FridayCalibrationReport(BaseModel):
    overall_ece: float | None
    per_target_type_calibration: dict[str, float]
    trend: Literal["improving", "degrading", "stable", "insufficient_data"]
    recent_accuracy_summary: dict[str, Any]


class FridayResolutionRequest(BaseModel):
    forecast_id: UUID
    observed_value: float
    event_occurred: bool | None = None
    ambiguity_note: str | None = None
    resolution_method: str = "automated_telemetry"


def _friday_forecast_response(
    req: FridayForecastRequest, forecast: Any
) -> FridayForecastResponse:
    """Build the delegation response for a forecast (fresh or replayed)."""
    prob = forecast.probability or 0.5
    prob_dist = forecast.predictive_distribution or {
        "exceedance_probability": prob,
        "p10": forecast.range_lower,
        "p50": forecast.prediction,
        "p90": forecast.range_upper,
    }
    drivers = [
        DriverItem(
            metric=d.name,
            correlation=d.strength if d.direction == "increases_risk" else -d.strength,
            lead_time="2h (lag-2 peak)" if d.leading_or_lagging == "leading" else "0h (concurrent)",
        )
        for d in forecast.drivers
    ]
    cal_score, cal_metrics = _honest_calibration(forecast)
    return FridayForecastResponse(
        futuris_forecast_id=forecast.forecast_id,
        friday_request_id=req.friday_request_id,
        prediction=PredictionPayload(
            point_estimate=forecast.prediction,
            lower_bound=forecast.range_lower,
            upper_bound=forecast.range_upper,
            probability_distribution=prob_dist,
        ),
        confidence=forecast.confidence.value.upper(),
        status=forecast.status.value,
        calibration_score=cal_score,
        calibration_metrics=cal_metrics,
        predictive_distribution=forecast.predictive_distribution or prob_dist,
        intervals=forecast.intervals
        or [{"lower": forecast.range_lower, "upper": forecast.range_upper}],
        model_metadata=forecast.model_metadata or {"model_version": forecast.model_version},
        prediction_is_not_authorization=True,
        executable_commands=[],
        assumptions=forecast.assumptions or ["Cached idempotent response"],
        evidence_snapshot_id=str(forecast.evidence[0].evidence_id)
        if forecast.evidence
        else "no_evidence_frozen",
        model_used=forecast.model_version,
        drivers_identified=drivers,
    )


@router.post(
    "/forecast",
    response_model=FridayForecastResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_friday_auth)],
)
async def delegate_forecast(
    req: FridayForecastRequest,
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> FridayForecastResponse:
    """Accept and orchestrate an operational forecast delegated from FRIDAY."""
    f_repo = ForecastRepository(session)
    raw_header_key = x_idempotency_key if isinstance(x_idempotency_key, str) else None
    # Idempotency precedence: explicit key > X-Idempotency-Key header > the
    # FRIDAY request id. FRIDAY retries a delegation with the same
    # friday_request_id when it does not see a reply, so that id must never
    # produce a second forecast.
    effective_idempotency_key = (
        req.idempotency_key or raw_header_key or f"friday_req:{req.friday_request_id}"
    )

    horizon_map = {
        "1h": timedelta(hours=1),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    h_delta = horizon_map[req.horizon]

    # 1. Idempotency Check
    if effective_idempotency_key:
        cached = await f_repo.get_by_idempotency_key(effective_idempotency_key)
        if cached:
            # A reused key is only a replay when the payload matches. Serving
            # the cached forecast for a *different* target would silently answer
            # a question nobody asked, so a mismatch is a loud conflict.
            if cached.target != req.target or cached.horizon != h_delta:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Idempotency key '{effective_idempotency_key}' was already used for "
                        f"target '{cached.target}' with horizon '{cached.horizon}', not "
                        f"'{req.target}' with '{req.horizon}'. Idempotency keys must not be "
                        "reused across different payloads."
                    ),
                )
            return _friday_forecast_response(req, cached)

    # 2. Check Staleness & Insufficient Data if telemetry data is provided
    t_data = req.telemetry_data or req.context.get("telemetry_data")
    if t_data is not None:
        try:
            check_insufficient_data(t_data, min_points=5, raise_error=True)
            if not req.allow_stale:
                check_data_staleness(t_data, max_staleness_seconds=3600.0, raise_error=True)
        except DataStalenessError as e:
            return FridayForecastResponse(
                futuris_forecast_id=uuid4(),
                friday_request_id=req.friday_request_id,
                prediction=PredictionPayload(
                    point_estimate=0.0,
                    lower_bound=0.0,
                    upper_bound=0.0,
                    probability_distribution={"exceedance_probability": 0.0},
                ),
                confidence="INSUFFICIENT_DATA",
                status="BLOCKED",
                calibration_score=0.0,
                calibration_metrics={"error": "stale_data", "detail": str(e)},
                predictive_distribution={},
                intervals={},
                model_metadata={"blocked_reason": "stale_data_detected", "error": str(e)},
                prediction_is_not_authorization=True,
                executable_commands=[],
                assumptions=[f"BLOCKED: Telemetry is stale. {e}"],
                evidence_snapshot_id="stale_telemetry_refused",
                model_used="none",
                drivers_identified=[],
            )
        except InsufficientDataError as e:
            return FridayForecastResponse(
                futuris_forecast_id=uuid4(),
                friday_request_id=req.friday_request_id,
                prediction=PredictionPayload(
                    point_estimate=0.0,
                    lower_bound=0.0,
                    upper_bound=0.0,
                    probability_distribution={"exceedance_probability": 0.0},
                ),
                confidence="INSUFFICIENT_DATA",
                status="INSUFFICIENT_DATA",
                calibration_score=0.0,
                calibration_metrics={"error": "insufficient_data", "detail": str(e)},
                predictive_distribution={},
                intervals={},
                model_metadata={"blocked_reason": "insufficient_data_detected", "error": str(e)},
                prediction_is_not_authorization=True,
                executable_commands=[],
                assumptions=[f"INSUFFICIENT_DATA: {e}"],
                evidence_snapshot_id="insufficient_data_refused",
                model_used="none",
                drivers_identified=[],
            )

    # 3. Research enrichment and pipeline run
    # FRIDAY delegates the target, not the evidence: FUTURIS is expected to bring
    # the ecosystem's research context to the forecast it returns. The fetch is
    # bounded (1.5 s) and best-effort, so a slow or absent research agent
    # degrades the enrichment, never the forecast.
    reports = await fetch_research_reports(req.target, as_of=datetime.now(UTC))
    research_findings = finding_labels(reports)

    pipeline = ForecastingPipeline()
    now = datetime.now(UTC)

    result = await pipeline.run(
        target=req.target,
        as_of=now,
        horizon=h_delta,
        lookback_days=14,
    )
    f = result.forecast
    f.idempotency_key = effective_idempotency_key

    # Research findings are attached as interpretability drivers and recorded in
    # metadata. They do not move the statistical estimate: the prediction still
    # comes from the pipeline, and its evidence class stays whatever the pipeline
    # established (``synthetic`` for the configured telemetry generator).
    for finding in research_findings[:2]:
        f.drivers.append(
            Driver(
                name=finding,
                direction="neutral",
                strength=0.5,
                leading_or_lagging="leading",
                evidence_refs=[item.evidence_id for item in f.evidence][:1],
            )
        )
    f.model_metadata = {
        **(f.model_metadata or {}),
        "intelx_context_included": bool(research_findings),
        "research_reports_considered": len(reports),
    }

    # 3.1 Persist. The unique index on idempotency_key is the arbiter when two
    # delegations with the same key race: the first insert wins and the loser
    # gets an IntegrityError, which is answered by replaying the winner -- the
    # retry storm produces exactly one forecast and N identical responses.
    try:
        await f_repo.create(f, idempotency_key=effective_idempotency_key)
    except (IntegrityError, OperationalError):
        from futuris.storage.db import safe_rollback

        await safe_rollback(session)
        winner = None
        for _ in range(20):
            try:
                winner = await f_repo.get_by_idempotency_key(effective_idempotency_key)
                if winner is not None:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.2)

        if winner is None or winner.target != req.target or winner.horizon != h_delta:
            raise
        logger.info(
            "friday_delegation_idempotency_replay",
            idempotency_key=effective_idempotency_key,
            replayed_forecast_id=str(winner.forecast_id),
        )
        return _friday_forecast_response(req, winner)


    # 4. Audit Log
    audit_logger = AuditLogger(session)
    await audit_logger.log_mutation(
        actor_label="friday_agent",
        action="generate_forecast",
        entity="forecast",
        entity_id=str(f.forecast_id),
        payload={"target": req.target, "horizon": req.horizon, "prediction": f.prediction},
    )
    committed = False
    for _ in range(10):
        try:
            await session.commit()
            committed = True
            break
        except OperationalError:
            await asyncio.sleep(0.1)
    if not committed:
        await session.commit()

    return _friday_forecast_response(req, f)



@router.post(
    "/delegate",
    response_model=FridayTaskEnvelopeResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_friday_auth)],
)
async def delegate_task(
    envelope: FridayTaskEnvelope,
    session: AsyncSession = Depends(get_db_session),
    outcome_repo: OutcomeRepository = Depends(get_outcome_repo),
    event_repo: EventRepository = Depends(get_event_repo),
) -> FridayTaskEnvelopeResponse:
    """Universal Task Protocol delegation endpoint for FRIDAY and Cortex."""
    t0 = time.time()
    action_lower = envelope.action.lower().strip()

    # Invariant: PREDICTION IS NOT AUTHORIZATION
    # Fail-closed if any mitigation execution, command execution, or website changes are requested
    forbidden_actions = {
        "execute",
        "execute_shell",
        "shell",
        "exec",
        "exec_command",
        "command",
        "bash",
        "sh",
        "run",
        "run_command",
        "script",
        "mitigate",
        "apply_mitigation",
        "remediate",
        "remediation",
        "failover",
        "website_change",
        "scale",
        "scale_up",
        "scale_down",
        "deploy",
        "restart",
        "stop",
        "kill",
        "delete",
        "drop",
        "write",
        "write_file",
        "upload",
        "ssh",
        "scp",
        "patch",
        "modify",
        "mutate",
    }
    payload = envelope.payload or {}
    if action_lower in forbidden_actions or any(
        k in payload
        for k in [
            "command",
            "commands",
            "cmd",
            "script",
            "bash_command",
            "shell_command",
            "exec",
            "code",
            "mitigation_command",
        ]
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Prediction is not authorization: Futuris does not execute mitigations, "
                "system commands, or website changes. It only provides calibrated forecasts."
            ),
        )

    # Route Actions
    if action_lower in ("forecast", "predict"):
        req = FridayForecastRequest(
            friday_request_id=envelope.task_id,
            target=payload.get("target") or payload.get("metric") or "system:service:capacity_24h",
            horizon=payload.get("horizon", "24h"),
            confidence_level=payload.get("confidence_level", 0.90),
            context=payload.get("context", {}),
            priority=envelope.priority if envelope.priority in ("normal", "urgent") else "normal",
            idempotency_key=envelope.idempotency_key or payload.get("idempotency_key"),
            telemetry_data=payload.get("telemetry_data"),
            allow_stale=payload.get("allow_stale", False),
        )
        f_resp = await delegate_forecast(req=req, x_idempotency_key=None, session=session)
        lat = int((time.time() - t0) * 1000)
        return FridayTaskEnvelopeResponse(
            task_id=envelope.task_id,
            action=envelope.action,
            status="SUCCESS" if f_resp.status != "BLOCKED" else "BLOCKED",
            result=f_resp.model_dump(mode="json"),
            summary=f"Forecast executed for target '{req.target}' with status '{f_resp.status}'.",
            prediction_is_not_authorization=True,
            executable_commands=[],
            execution_time_ms=lat,
        )

    if action_lower == "scenario":
        base_id = payload.get("base_forecast_id")
        if not base_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="scenario action requires 'base_forecast_id' in payload.",
            )
        scen_spec_data = payload.get("scenario_spec", {})
        scen_req = FridayScenarioRequest(
            question=payload.get("question", "What-if scenario analysis"),
            base_forecast_id=UUID(str(base_id)),
            scenario_spec=FridayScenarioSpec(
                name=scen_spec_data.get("name", "scenario_analysis"),
                variable_changes=[
                    VariableChange(**vc) for vc in scen_spec_data.get("variable_changes", [])
                ],
                assumptions=scen_spec_data.get("assumptions", []),
            ),
        )
        s_resp = await evaluate_scenario(scen_req, session=session)
        lat = int((time.time() - t0) * 1000)
        return FridayTaskEnvelopeResponse(
            task_id=envelope.task_id,
            action=envelope.action,
            status="SUCCESS",
            result=s_resp.model_dump(mode="json"),
            summary="Scenario evaluated for base forecast '{base_id}'. Risk: "
                "{s_resp.risk_assessment}.",
            prediction_is_not_authorization=True,
            executable_commands=[],
            execution_time_ms=lat,
        )

    if action_lower == "resolve":
        f_id = payload.get("forecast_id")
        obs = payload.get("observed_value")
        if f_id is None or obs is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="resolve action requires 'forecast_id' and 'observed_value'.",
            )
        res_req = FridayResolutionRequest(
            forecast_id=UUID(str(f_id)),
            observed_value=float(obs),
            event_occurred=payload.get("event_occurred"),
            ambiguity_note=payload.get("ambiguity_note"),
            resolution_method=payload.get("resolution_method", "automated_telemetry"),
        )
        res_resp = await resolve_forecast(
            req=res_req, session=session, outcome_repo=outcome_repo, event_repo=event_repo
        )
        lat = int((time.time() - t0) * 1000)
        return FridayTaskEnvelopeResponse(
            task_id=envelope.task_id,
            action=envelope.action,
            status="SUCCESS",
            result=res_resp,
            summary=f"Forecast '{f_id}' resolved against observed outcome {obs}.",
            prediction_is_not_authorization=True,
            executable_commands=[],
            execution_time_ms=lat,
        )

    if action_lower == "cancel":
        f_id = payload.get("forecast_id")
        if not f_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="cancel action requires 'forecast_id'.",
            )
        cancel_resp = await cancel_forecast(
            forecast_id=UUID(str(f_id)), session=session, event_repo=event_repo
        )
        lat = int((time.time() - t0) * 1000)
        return FridayTaskEnvelopeResponse(
            task_id=envelope.task_id,
            action=envelope.action,
            status="SUCCESS",
            result=cancel_resp,
            summary=f"Forecast '{f_id}' cancelled successfully.",
            prediction_is_not_authorization=True,
            executable_commands=[],
            execution_time_ms=lat,
        )

    if action_lower in ("calibration", "status"):
        cal_resp = await get_friday_calibration(outcome_repo=outcome_repo)
        lat = int((time.time() - t0) * 1000)
        return FridayTaskEnvelopeResponse(
            task_id=envelope.task_id,
            action=envelope.action,
            status="SUCCESS",
            result=cal_resp.model_dump(mode="json"),
            summary=f"Calibration score ECE: {cal_resp.overall_ece}, trend: {cal_resp.trend}.",
            prediction_is_not_authorization=True,
            executable_commands=[],
            execution_time_ms=lat,
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported FRIDAY TaskEnvelope action: '{envelope.action}'.",
    )


@router.post(
    "/scenario",
    response_model=FridayScenarioResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_friday_auth)],
)
async def evaluate_scenario(
    req: FridayScenarioRequest,
    session: AsyncSession = Depends(get_db_session),
) -> FridayScenarioResponse:
    """Evaluate what-if counterfactual scenarios for FRIDAY orchestrator."""
    f_repo = ForecastRepository(session)
    s_repo = ScenarioRepository(session)
    scenario_engine = ScenarioEngine(scenario_repo=s_repo)

    base = await f_repo.get(req.base_forecast_id)
    if not base:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Base forecast '{req.base_forecast_id}' not found.",
        )

    overrides = {}
    for vc in req.scenario_spec.variable_changes:
        overrides[vc.metric] = base.prediction * (1.0 + vc.change_pct / 100.0)

    is_stress = any(vc.change_pct > 30 for vc in req.scenario_spec.variable_changes)
    spec = ScenarioSpec(
        spec_id=uuid4(),
        name=req.question,
        scenario_type=ScenarioType.STRESS if is_stress else ScenarioType.USER_DEFINED,
        assumption_overrides=overrides,
        rationale=req.question,
    )

    s_res = await scenario_engine.run_scenario(base_forecast=base, spec=spec)
    comparison = scenario_engine.compare(base, [s_res])

    # Dynamic scenario divergence extraction across all perturbed metrics
    div_pred = base.prediction
    if s_res.perturbed_values:
        values = list(s_res.perturbed_values.values())
        div_pred = sum(values) / len(values)

    delta_pct = ((div_pred - base.prediction) / base.prediction) * 100.0 if base.prediction else 0.0

    risk = "HIGH_RISK" if delta_pct > 25.0 else "MODERATE_RISK" if delta_pct > 10.0 else "NOMINAL"

    # Audit Log
    audit_logger = AuditLogger(session)
    await audit_logger.log_mutation(
        actor_label="friday_agent",
        action="evaluate_scenario",
        entity="scenario",
        entity_id=str(s_res.spec.spec_id),
        payload={
            "base_forecast_id": str(base.forecast_id),
            "risk_assessment": risk,
            "delta_pct": delta_pct,
        },
    )
    await session.commit()

    return FridayScenarioResponse(
        scenario_id=s_res.spec.spec_id,
        divergent_prediction=round(div_pred, 2),
        probability_outcome=0.85 if delta_pct > 20.0 else (base.probability or 0.5),
        risk_assessment=risk,
        comparison_to_baseline={
            "delta_absolute": round(div_pred - base.prediction, 2),
            "delta_percentage": round(delta_pct, 2),
            "baseline_prediction": base.prediction,
            "variable_matrix": comparison.variable_matrix,
        },
        assumptions=req.scenario_spec.assumptions,
        scenario_spec=req.scenario_spec.model_dump(mode="json"),
        prediction_is_not_authorization=True,
        executable_commands=[],
    )


@router.post(
    "/forecasts/{forecast_id}/cancel",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_friday_auth)],
)
async def cancel_forecast(
    forecast_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    event_repo: EventRepository = Depends(get_event_repo),
) -> dict[str, Any]:
    """Cancel an ongoing or active forecast."""
    f_repo = ForecastRepository(session)
    forecast = await f_repo.get(forecast_id)
    if not forecast:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Forecast '{forecast_id}' not found.",
        )

    # Cancellation is a lifecycle transition, not an overwrite: a forecast that
    # already reached a terminal state (resolved, invalidated, expired) keeps
    # that state and its outcome.
    cancelled = await f_repo.transition_status(
        forecast_id, {ForecastStatus.ACTIVE, ForecastStatus.DRAFT}, ForecastStatus.CANCELLED
    )
    if cancelled is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Forecast '{forecast_id}' is '{forecast.status.value}' and cannot be "
                "cancelled; only active or draft forecasts can be cancelled."
            ),
        )

    # Audit Log
    audit_logger = AuditLogger(session)
    await audit_logger.log_mutation(
        actor_label="friday_agent",
        action="cancel_forecast",
        entity="forecast",
        entity_id=str(forecast_id),
        payload={"previous_status": forecast.status.value, "new_status": "CANCELLED"},
    )

    # Event Log
    event = ForecastEvent(
        event_id=uuid4(),
        forecast_id=forecast_id,
        event_type=ForecastEventType.FORECAST_CANCELLED,
        payload={"cancelled_at": datetime.now(UTC).isoformat(), "target": forecast.target},
        emitted_at=datetime.now(UTC),
    )
    await event_repo.append(event)
    await session.commit()

    return {
        "forecast_id": forecast_id,
        "status": "CANCELLED",
        "cancelled_at": datetime.now(UTC),
        "prediction_is_not_authorization": True,
        "executable_commands": [],
    }


@router.post(
    "/forecasts/{forecast_id}/resolve",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_friday_auth)],
)
@router.post(
    "/resolution",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_friday_auth)],
)
async def resolve_forecast(
    req: FridayResolutionRequest,
    session: AsyncSession = Depends(get_db_session),
    outcome_repo: OutcomeRepository = Depends(get_outcome_repo),
    event_repo: EventRepository = Depends(get_event_repo),
) -> dict[str, Any]:
    """Resolve ground-truth outcome for a forecast and update calibration metrics."""
    f_repo = ForecastRepository(session)
    forecast = await f_repo.get(req.forecast_id)
    if not forecast:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Forecast '{req.forecast_id}' not found.",
        )

    # Only a live forecast accepts an outcome; the outcomes table's unique
    # constraint is the final guard when two resolutions race.
    if forecast.status not in (ForecastStatus.ACTIVE, ForecastStatus.DRAFT):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Forecast '{req.forecast_id}' is '{forecast.status.value}' and cannot be "
                "resolved; only active or draft forecasts accept outcomes."
            ),
        )

    existing = await outcome_repo.get_by_forecast(req.forecast_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Forecast {req.forecast_id} already has an outcome recorded at "
                f"{existing.resolved_at.isoformat()}; outcomes are immutable."
            ),
        )

    res_method = ResolutionMethod.AUTOMATED_TELEMETRY
    if req.resolution_method.lower() in ("human", "manual"):
        res_method = ResolutionMethod.HUMAN

    outcome = Outcome(
        outcome_id=uuid4(),
        forecast_id=req.forecast_id,
        observed_value=req.observed_value,
        event_occurred=req.event_occurred,
        resolved_at=datetime.now(UTC),
        resolution_method=res_method,
        ambiguity_note=req.ambiguity_note,
        resolution_rule_version="friday_resolution:v1",
    )
    saved_outcome = await outcome_repo.record_outcome(outcome)
    await f_repo.update_status(req.forecast_id, ForecastStatus.RESOLVED)

    await AuditLogger(session).log_mutation(
        actor_label="friday_agent",
        action="resolve_forecast",
        entity="outcome",
        entity_id=str(saved_outcome.outcome_id),
        payload={
            "forecast_id": str(req.forecast_id),
            "observed_value": saved_outcome.observed_value,
            "event_occurred": saved_outcome.event_occurred,
            "resolution_method": saved_outcome.resolution_method.value,
        },
    )

    # Recompute calibration from the real (predicted probability, outcome) pairs
    # stored in this database. Inventing probabilities to pair with outcomes
    # would make the calibration numbers describe a model that never ran.
    paired = await outcome_repo.list_resolved_with_forecasts(limit=2000)
    probs: list[float] = []
    actuals: list[bool] = []
    for paired_forecast, paired_outcome in paired:
        if paired_forecast.probability is None or paired_outcome.event_occurred is None:
            continue
        probs.append(paired_forecast.probability)
        actuals.append(bool(paired_outcome.event_occurred))

    cal_metrics = update_calibration_metrics(probs, actuals)

    # Audit Log
    audit_logger = AuditLogger(session)
    await audit_logger.log_mutation(
        actor_label="friday_agent",
        action="resolve_forecast",
        entity="outcome",
        entity_id=str(saved_outcome.outcome_id),
        payload={
            "forecast_id": str(req.forecast_id),
            "observed_value": req.observed_value,
            "event_occurred": req.event_occurred,
            "brier_score": cal_metrics.get("brier_score"),
        },
    )

    # Event Log
    event = ForecastEvent(
        event_id=uuid4(),
        forecast_id=req.forecast_id,
        event_type=ForecastEventType.FORECAST_OUTCOME_RECORDED,
        payload=saved_outcome.model_dump(mode="json"),
        emitted_at=datetime.now(UTC),
    )
    await event_repo.append(event)
    await session.commit()

    error = (
        abs(req.observed_value - forecast.prediction) if forecast.prediction is not None else 0.0
    )

    return {
        "forecast_id": req.forecast_id,
        "outcome_id": saved_outcome.outcome_id,
        "status": "RESOLVED",
        "observed_value": req.observed_value,
        "predicted_value": forecast.prediction,
        "absolute_error": round(error, 4),
        "event_occurred": req.event_occurred,
        "resolved_at": outcome.resolved_at,
        "updated_calibration_metrics": cal_metrics,
        "prediction_is_not_authorization": True,
        "executable_commands": [],
    }


@router.get(
    "/forecasts",
    dependencies=[Depends(verify_friday_auth)],
)
async def list_friday_forecasts(
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List active and recent forecasts with status and accuracy tracking."""
    stmt = select(ForecastModel).order_by(ForecastModel.as_of.desc()).limit(limit)
    res = await session.execute(stmt)
    models = res.scalars().all()

    out = []
    for m in models:
        out.append(
            {
                "forecast_id": m.forecast_id,
                "target": m.target,
                "as_of": m.as_of,
                "prediction": m.prediction,
                "probability": m.probability,
                "confidence": m.confidence,
                "status": m.status,
                "model_version": m.model_version,
            }
        )
    return out


@router.get(
    "/calibration",
    response_model=FridayCalibrationReport,
    dependencies=[Depends(verify_friday_auth)],
)
async def get_friday_calibration(
    outcome_repo: OutcomeRepository = Depends(get_outcome_repo),
) -> FridayCalibrationReport:
    """Return FRIDAY calibration metrics calculated only from resolved forecasts."""
    analyzer = CalibrationAnalyzer()
    pairs = await outcome_repo.list_resolved_with_forecasts(limit=5000)
    samples = [
        (forecast, outcome)
        for forecast, outcome in pairs
        if forecast.probability is not None and outcome.event_occurred is not None
    ]
    probs = [float(forecast.probability) for forecast, _ in samples]
    actuals = [bool(outcome.event_occurred) for _, outcome in samples]

    target_samples: dict[str, list[tuple[float, bool]]] = {}
    for forecast, outcome in samples:
        target_samples.setdefault(forecast.target, []).append(
            (float(forecast.probability), bool(outcome.event_occurred))
        )

    per_target_ece = {
        target: analyzer.compute_expected_calibration_error(
            [probability for probability, _ in target_pairs],
            [actual for _, actual in target_pairs],
        )
        for target, target_pairs in target_samples.items()
    }

    interval_samples = [
        (forecast, outcome)
        for forecast, outcome in pairs
        if outcome.observed_value is not None
    ]
    coverage = (
        sum(
            forecast.range_lower <= outcome.observed_value <= forecast.range_upper
            for forecast, outcome in interval_samples
        )
        / len(interval_samples)
        if interval_samples
        else None
    )

    declared_interval_results: dict[float, list[bool]] = {}
    for forecast, outcome in interval_samples:
        observed_value = outcome.observed_value
        intervals = getattr(forecast, "intervals", None)
        if (
            isinstance(observed_value, bool)
            or not isinstance(observed_value, (int, float))
            or not isinstance(intervals, list)
        ):
            continue
        try:
            observed_value = float(observed_value)
        except (OverflowError, ValueError):
            continue
        if not math.isfinite(observed_value):
            continue

        intervals_by_level: dict[float, list[tuple[float, float]]] = {}
        for interval in intervals:
            if not isinstance(interval, dict):
                continue
            level = interval.get("nominal_coverage")
            lower = interval.get("lower")
            upper = interval.get("upper")
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in (level, lower, upper)
            ):
                continue
            try:
                level, lower, upper = map(float, (level, lower, upper))
            except (OverflowError, ValueError):
                continue
            if not all(math.isfinite(value) for value in (level, lower, upper)):
                continue
            if not 0.0 < level < 1.0 or lower > upper:
                continue
            intervals_by_level.setdefault(float(level), []).append((lower, upper))

        # Outcomes are forecast-level observations. Multiple intervals at the
        # same nominal level (for example, per-step bounds) cannot be paired
        # unambiguously with that single observation, so exclude that level.
        for level, bounds in intervals_by_level.items():
            if len(bounds) == 1:
                lower, upper = bounds[0]
                declared_interval_results.setdefault(level, []).append(
                    lower <= observed_value <= upper
                )

    nominal_interval_coverage = {
        f"{level:g}": {
            "empirical_coverage": round(sum(results) / len(results), 4),
            "sample_count": len(results),
        }
        for level, results in sorted(declared_interval_results.items())
    }

    trend: Literal["improving", "degrading", "stable", "insufficient_data"] = (
        "insufficient_data"
    )
    if len(samples) >= 10:
        split_at = len(samples) // 2
        earlier_ece = analyzer.compute_expected_calibration_error(
            probs[:split_at], actuals[:split_at]
        )
        recent_ece = analyzer.compute_expected_calibration_error(
            probs[split_at:], actuals[split_at:]
        )
        if recent_ece < earlier_ece - 0.01:
            trend = "improving"
        elif recent_ece > earlier_ece + 0.01:
            trend = "degrading"
        else:
            trend = "stable"

    return FridayCalibrationReport(
        overall_ece=(
            analyzer.compute_expected_calibration_error(probs, actuals) if samples else None
        ),
        per_target_type_calibration=per_target_ece,
        trend=trend,
        recent_accuracy_summary={
            "brier_score": analyzer.compute_brier_score(probs, actuals) if samples else None,
            "range_coverage": coverage,
            "interval_sample_count": len(interval_samples),
            "nominal_interval_coverage": nominal_interval_coverage,
            "resolved_samples": len(samples),
            "calibration_status": "measured" if samples else "uncalibrated",
        },
    )
