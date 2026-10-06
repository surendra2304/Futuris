"""Universe forecast generation and refresh pacing.

Domain logic for the FRIDAY Universe predictive matrix: how a forecast for a
target is derived, and how a refresh across every target is paced. The API router
owns only the request/response shape and delegates here.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from futuris.core.enums import (
    ConfidenceLevel,
    EvidenceClass,
    ForecastStatus,
    SignalClass,
    SourceTrust,
)
from futuris.core.hashing import content_hash_of
from futuris.core.schemas import Driver, EvidenceRef, Forecast
from futuris.core.universe_domains import UNIVERSE_TARGETS, DomainTargetSpec, get_target_spec
from futuris.infra.logging import get_logger
from futuris.storage.repositories import ForecastRepository

logger = get_logger("futuris.core.universe_forecasting")

# Refreshing every target performs an outbound IntelX context call, and those calls
# are serial: each one can burn its full client timeout when IntelX is down. The
# first version of this loop had no bound, which turned one dead peer into a 61s
# request. The budget is owned here so the pacing rule lives with the work it
# paces rather than in whichever route happens to call it.
REFRESH_BUDGET_SECONDS = 15.0

#: Slack added to a target's deadline so the timer never cancels an in-flight
#: database write.
WRITE_GRACE_SECONDS = 2.0


async def generate_universe_forecast(
    target: str,
    context: dict[str, Any] | None = None,
    session: AsyncSession | None = None,
    skip_intelx: bool = False,
    model_budget_seconds: float | None = None,
) -> Forecast:
    """Produce a forecast for any FRIDAY Universe target.

    There are exactly two honest ways to obtain a number here, and each is
    labelled with the provenance the client renders:

    * the caller supplies the estimate (``context.point_estimate`` /
      ``context.current_value`` / ``context.probability``) -- the number is
      echoed back and marked ``synthetic`` with the caller named as the source;
    * nobody supplies one -- the forecasting pipeline computes a number from the
      configured telemetry generator and the forecast is marked ``synthetic``
      with that generator named as the source.

    When neither path can produce a number the forecast is returned in
    ``insufficient_data`` status with the reason recorded, never a placeholder
    value standing in for a forecast.
    """
    spec = get_target_spec(target)
    now = datetime.now(UTC)
    ctx = dict(context or {})

    intelx_findings = await _fetch_intelx_context(target, now) if not skip_intelx else []

    supplied = _caller_supplied_values(ctx)
    if supplied.must_emit:
        forecast = _forecast_from_caller_context(spec, supplied, ctx, now, intelx_findings)
    else:
        forecast = await _forecast_from_pipeline(
            spec, target, now, intelx_findings, model_budget_seconds=model_budget_seconds
        )

    if session is not None:
        repo = ForecastRepository(session)
        await repo.create(forecast)
        await session.commit()

    return forecast


@dataclass
class _CallerValues:
    """Values the caller explicitly supplied, if any."""

    point: float | None = None
    probability: float | None = None
    range_lower: float | None = None
    range_upper: float | None = None

    @property
    def must_emit(self) -> bool:
        return self.point is not None or self.probability is not None


def _caller_supplied_values(ctx: dict[str, Any]) -> _CallerValues:
    point = ctx.get("point_estimate", ctx.get("current_value"))
    lower = ctx.get("range_lower")
    upper = ctx.get("range_upper")
    return _CallerValues(
        point=float(point) if point is not None else None,
        probability=float(ctx["probability"]) if ctx.get("probability") is not None else None,
        range_lower=float(lower) if lower is not None else None,
        range_upper=float(upper) if upper is not None else None,
    )


async def _fetch_intelx_context(target: str, as_of: datetime) -> list[str]:
    """Fetch live IntelX research context, tolerating an unreachable peer."""
    from futuris.infra.research_context import fetch_research_reports, finding_labels

    return finding_labels(await fetch_research_reports(target, as_of))


def _context_drivers(ctx: dict[str, Any], evidence_id: UUID) -> list[Driver]:
    """Drivers named after the context keys the caller actually supplied."""
    ignored = {"point_estimate", "current_value", "probability", "range_lower", "range_upper"}
    drivers: list[Driver] = []
    for key, value in ctx.items():
        if key in ignored or isinstance(value, dict | list):
            continue
        drivers.append(
            Driver(
                name=f"context.{key}={value}",
                direction="neutral",
                strength=0.5,
                leading_or_lagging="leading",
                evidence_refs=[evidence_id],
            )
        )
    return drivers


def _forecast_from_caller_context(
    spec: DomainTargetSpec,
    supplied: _CallerValues,
    ctx: dict[str, Any],
    now: datetime,
    intelx_findings: list[str],
) -> Forecast:
    """Echo caller-supplied estimates, labelled for exactly what they are."""
    point = supplied.point if supplied.point is not None else (supplied.probability or 0.0) * 100.0
    probability = supplied.probability
    assumptions = ["caller_supplied_context"]

    lower, upper = supplied.range_lower, supplied.range_upper
    if lower is None or upper is None:
        # No interval was supplied. The +/-10% band below is a stated policy,
        # recorded in `assumptions`, not a measured uncertainty.
        half_span = abs(point) * 0.10 or 1.0
        lower = point - half_span if lower is None else lower
        upper = point + half_span if upper is None else upper
        assumptions.append("range_derived_as_+/-10%_of_caller_estimate")

    evidence_id = uuid4()
    evidence = EvidenceRef(
        evidence_id=evidence_id,
        source="caller_context",
        source_trust=SourceTrust.MEDIUM,
        signal_class=SignalClass.HUMAN_INPUT,
        as_of=now,
        snapshot_path=f"inline://universe/{spec.target}/caller-context.json",
        content_hash=content_hash_of(
            {"target": spec.target, "as_of": now.isoformat(), "context": ctx}
        ),
        evidence_class=EvidenceClass.SYNTHETIC,
    )

    drivers = _context_drivers(ctx, evidence_id)
    drivers.extend(
        Driver(
            name=finding,
            direction="neutral",
            strength=0.5,
            leading_or_lagging="leading",
            evidence_refs=[evidence_id],
        )
        for finding in intelx_findings[:2]
    )

    return Forecast(
        forecast_id=uuid4(),
        target=spec.target,
        as_of=now,
        horizon=timedelta(hours=24),
        expires_at=now + timedelta(hours=24),
        review_at=now + timedelta(hours=6),
        prediction=point,
        range_lower=lower,
        range_upper=upper,
        probability=probability,
        confidence=ConfidenceLevel.LOW,
        drivers=drivers,
        evidence=[evidence],
        assumptions=assumptions,
        model_version=spec.default_model,
        status=ForecastStatus.ACTIVE,
        evidence_class=EvidenceClass.SYNTHETIC,
        evidence_source="caller_supplied_context",
        model_metadata={
            "source": "caller_supplied_context",
            "caller_supplied_fields": sorted(
                k for k in ctx if k in {"point_estimate", "current_value", "probability"}
            ),
            "calibration_notes": (
                "No resolved outcomes back this target; confidence is reported LOW "
                "because calibration quality is unmeasured, not because the estimate "
                "is likely wrong."
            ),
            "intelx_context_included": bool(intelx_findings),
            "prediction_is_not_authorization": True,
        },
    )


async def _forecast_from_pipeline(
    spec: DomainTargetSpec,
    target: str,
    now: datetime,
    intelx_findings: list[str],
    model_budget_seconds: float | None = None,
) -> Forecast:
    """Compute the forecast from configured telemetry, or refuse honestly."""
    from futuris.core.pipeline import ForecastingPipeline

    try:
        pipeline = ForecastingPipeline()
        result = await pipeline.run(
            target=target,
            as_of=now,
            horizon=timedelta(hours=24),
            lookback_days=14,
            capacity_threshold=spec.high_risk_threshold,
            model_budget_seconds=model_budget_seconds,
        )
    except Exception as exc:
        logger.warning("universe_forecast_insufficient_data", target=target, error=str(exc))
        return _insufficient_data_forecast(spec, now, error=exc, intelx_findings=intelx_findings)

    forecast = result.forecast
    forecast.evidence_class = EvidenceClass.SYNTHETIC
    forecast.evidence_source = "synthetic_telemetry_generator"
    forecast.assumptions = [
        *forecast.assumptions,
        "numbers derived from the configured telemetry generator, not from measured production "
            "telemetry",
    ]
    forecast.model_metadata = {
        **forecast.model_metadata,
        "intelx_context_included": bool(intelx_findings),
        "stage_durations_ms": result.stage_durations_ms,
    }
    for finding in intelx_findings[:2]:
        forecast.drivers.append(
            Driver(
                name=finding,
                direction="neutral",
                strength=0.5,
                leading_or_lagging="leading",
                evidence_refs=[d.evidence_id for d in forecast.evidence][:1],
            )
        )
    return forecast


def _insufficient_data_forecast(
    spec: DomainTargetSpec,
    now: datetime,
    *,
    error: Exception,
    intelx_findings: list[str] | None = None,
) -> Forecast:
    """Return a forecast that states it has no numbers, instead of inventing them."""
    reason = f"{type(error).__name__}: {error}"
    return Forecast(
        forecast_id=uuid4(),
        target=spec.target,
        as_of=now,
        horizon=timedelta(hours=24),
        expires_at=now + timedelta(hours=24),
        review_at=now + timedelta(hours=6),
        prediction=0.0,
        range_lower=0.0,
        range_upper=0.0,
        probability=None,
        confidence=ConfidenceLevel.INSUFFICIENT_DATA,
        drivers=[],
        evidence=[],
        assumptions=[f"INSUFFICIENT_DATA: {reason}"],
        model_version="none",
        status=ForecastStatus.INSUFFICIENT_DATA,
        evidence_class=EvidenceClass.SYNTHETIC,
        evidence_source=None,
        model_metadata={
            "blocked_reason": "insufficient_data_detected",
            "error": reason,
            "intelx_context_included": bool(intelx_findings),
            "prediction_is_not_authorization": True,
        },
        calibration_metrics={"error": "insufficient_data", "detail": reason},
    )


async def _paced_pass(
    session: AsyncSession,
    targets: list[str],
    budget_seconds: float,
    sink: dict[str, Any] | None = None,
) -> int:
    """Generate a forecast for each target in order, within ``budget_seconds``.

    Sequential rather than concurrent because each forecast persists through the
    caller's session. ``sink``, when given, collects the forecasts by target.
    Returns how many were generated; a target that exhausts the budget stops the
    pass, so the caller waits a bounded time instead of one client timeout each.
    """
    started = time.monotonic()
    generated = 0

    for target in targets:
        remaining = budget_seconds - (time.monotonic() - started)
        if remaining <= 0:
            logger.info(
                "refresh_budget_exhausted",
                generated=generated,
                pending=len(targets) - generated,
            )
            break
        try:
            # The compute is already bounded by ``model_budget_seconds``; the
            # grace period exists so the timer does not cancel the tiny write
            # that follows a fit. Cancelling a flush invalidates the connection
            # and leaves the session unusable, which is far worse than finishing
            # a fraction of a second late.
            forecast = await asyncio.wait_for(
                generate_universe_forecast(
                    target, session=session, model_budget_seconds=max(0.1, remaining - 0.25)
                ),
                timeout=remaining + WRITE_GRACE_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "refresh_target_timed_out",
                target=target,
                budget_seconds=budget_seconds,
            )
            await _recover_session(session)
            break
        if sink is not None:
            sink[target] = forecast
        generated += 1

    return generated


async def _recover_session(session: AsyncSession) -> None:
    """Clear the pending-rollback state a cancelled write leaves behind.

    ``asyncio.wait_for`` can cancel a target in the middle of its INSERT, after
    which the session refuses all further use. That used to turn a bounded
    refresh into a 500 on the matrix pass. Only the cancelled target's write is
    lost: every completed target was committed by
    :func:`generate_universe_forecast`.
    """
    if getattr(session, "is_active", True):
        return
    try:
        await session.rollback()
        logger.info("refresh_session_recovered_from_cancelled_write")
    except Exception as exc:
        logger.warning("refresh_session_recovery_failed", error=str(exc))


async def refresh_all_within_budget(
    session: AsyncSession,
    *,
    budget_seconds: float = REFRESH_BUDGET_SECONDS,
) -> int:
    """Regenerate the forecast for every universe target, within the budget."""
    return await _paced_pass(session, list(UNIVERSE_TARGETS), budget_seconds)


async def refresh_missing_within_budget(
    session: AsyncSession,
    present: dict[str, Any],
    *,
    budget_seconds: float = REFRESH_BUDGET_SECONDS,
) -> int:
    """Backfill universe targets that have no active forecast yet.

    ``present`` is updated in place so a caller can assemble the matrix from it.
    """
    missing = [t for t in UNIVERSE_TARGETS if t not in present]
    return await _paced_pass(session, missing, budget_seconds, sink=present)
