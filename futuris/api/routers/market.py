"""Market forecasting: Stratex telemetry, IntelX context and Memora persistence."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.api.deps import get_db_session, get_forecast_repo
from futuris.api.errors import FuturisAPIError
from futuris.connectors.intelx_context import IntelXContextInjector, IntelXResearchReport
from futuris.connectors.trading_bot import TradingBotConnector
from futuris.core.enums import (
    ConfidenceLevel,
    EvidenceClass,
    ForecastStatus,
    SignalClass,
    SourceTrust,
)
from futuris.core.hashing import content_hash_of
from futuris.core.schemas import Driver, EvidenceRef, Forecast
from futuris.ecosystem.adapters import ecosystem_adapter
from futuris.infra.audit import AuditLogger
from futuris.infra.auth import AllowAnonymousRead, RequireAnalyst
from futuris.infra.config import settings
from futuris.infra.logging import get_logger
from futuris.integrations.memora_forecast_publisher import (
    MemoraForecastPublishError,
    publish_forecast_advisory,
)
from futuris.storage.db import safe_rollback
from futuris.storage.repositories import ForecastRepository, OutcomeRepository

logger = get_logger("futuris.api.market")

router = APIRouter(tags=["Market and Stratex Forecasting"])


class MarketForecastRequest(BaseModel):
    """Payload sent by Stratex FuturisMarketClient or ecosystem trading orchestrators."""

    symbol: str = Field(
        default="BTCUSDT", description="Trading asset symbol (e.g. BTCUSDT, ETHUSDT)"
    )
    horizons: list[str] = Field(default_factory=lambda: ["24h"], description="Forecast horizons")
    include_intelx: bool = Field(default=True, description="Enrich with IntelX exogenous research")
    include_inference: bool = Field(
        default=True, description="Ground with multi-agent Inference Gateway"
    )


class VolatilityForecastPayload(BaseModel):
    probability: float = Field(..., description="Probability of elevated volatility spike")
    confidence: float = Field(..., description="Meta-confidence score")
    horizon_hours: int = Field(default=24, description="Horizon in hours")
    range_pct: list[float] = Field(
        default_factory=lambda: [-3.0, 3.0], description="Expected price volatility range %"
    )
    expected_volatility: float = Field(
        default=0.035, description="Annualized or daily standard deviation estimate"
    )
    drivers: list[str] = Field(default_factory=list, description="Primary causal drivers")


class DrawdownRiskPayload(BaseModel):
    probability: float = Field(
        ..., description="Probability of exceeding maximum allowable drawdown threshold"
    )
    threshold_pct: float = Field(default=0.05, description="Drawdown trigger threshold (5%)")
    horizon_hours: int = Field(default=24, description="Horizon in hours")
    risk_level: str = Field(default="LOW", description="LOW | MEDIUM | HIGH | CRITICAL")


class RegimeOutlookPayload(BaseModel):
    current: str = Field(
        ...,
        description="Current detected market regime: TRENDING_BULL | TRENDING_BEAR | RANGING | "
            "HIGH_VOLATILITY",
    )
    transition_probability: float = Field(
        ..., description="Likelihood of regime shift over horizon"
    )
    predicted_direction: str = Field(..., description="UPWARD | DOWNWARD | SIDEWAYS | VOLATILE")
    rationale: str = Field(
        ...,
        description="Explanatory synthesis of quantitative telemetry and IntelX exogenous context",
    )


class MarketForecastResponse(BaseModel):
    status: str = "OK"
    symbol: str
    volatility_forecast: VolatilityForecastPayload
    drawdown_risk: DrawdownRiskPayload
    regime_outlook: RegimeOutlookPayload
    intelx_context_included: bool
    inference_grounded: bool
    forecast_id: str
    timestamp: str
    memora_event_id: str | None = None
    evidence_class: EvidenceClass = EvidenceClass.SYNTHETIC
    evidence_source: str | None = None


class MarketAccuracyResponse(BaseModel):
    total_evaluated: int
    accuracy_pct: float
    brier_score: float
    status: str
    recent_records: list[dict[str, Any]] = []
    evidence_class: EvidenceClass | None = None
    sample_note: str | None = None


async def _generate_market_prediction(
    symbol: str,
    horizon_hours: int = 24,
    include_intelx: bool = True,
    include_inference: bool = True,
    forecast_repo: ForecastRepository | None = None,
    intelx_catalyst: tuple[str, str] | None = None,
    actor_label: str = "system",
) -> MarketForecastResponse:
    """Combine Stratex telemetry, IntelX research and Inference into a market forecast."""
    clean_sym = symbol.upper().strip() if symbol else "BTCUSDT"
    now = datetime.now(UTC)
    forecast_id = uuid4()

    # 1. Ingest IntelX Exogenous Market Intelligence
    intelx_reports = []
    exogenous_adj = {
        "sentiment_multiplier": 1.0,
        "volatility_multiplier": 1.0,
        "confidence_penalty": 0.0,
    }
    top_findings = []
    if include_intelx:
        intelx_client = IntelXContextInjector(
            base_url=settings.INTELX_URL,
            api_key=settings.INTELX_API_KEY,
        )
        try:
            intelx_reports = await intelx_client.fetch_recent_research(
                asset_or_sector=clean_sym,
                lookback_days=7,
                as_of=now,
            )
        except Exception as exc:
            logger.warning("intelx_market_query_failed", symbol=clean_sym, error=type(exc).__name__)
        try:
            intelx_reports.extend(
                await intelx_client.fetch_durable_notice_context(
                    asset_or_sector=clean_sym,
                    lookback_days=7,
                    as_of=now,
                )
            )
        except Exception as exc:
            logger.warning(
                "intelx_notice_inbox_unavailable", symbol=clean_sym, error=type(exc).__name__
            )
        if intelx_reports:
            intelx_reports.sort(key=lambda report: report.published_at, reverse=True)
            exogenous_adj = intelx_client.compute_exogenous_adjustments(intelx_reports)
            top_findings = intelx_reports[0].key_findings
            logger.info(
                "intelx_market_context_acquired",
                symbol=clean_sym,
                sentiment=intelx_reports[0].sentiment_score,
                findings_count=len(top_findings),
            )
    if intelx_catalyst:
        catalyst_id, catalyst_summary = intelx_catalyst
        from uuid import NAMESPACE_URL, uuid5

        intelx_reports.append(
            IntelXResearchReport(
                report_id=uuid5(NAMESPACE_URL, catalyst_id),
                asset_or_sector=clean_sym,
                published_at=now,
                summary=catalyst_summary,
                sentiment_score=0.0,
                volatility_impact_factor=1.0,
                key_findings=[catalyst_summary],
                tags=["intelx", "webhook_catalyst"],
            )
        )
        top_findings = [catalyst_summary]

    # 2. Ingest Stratex Market Telemetry
    trading_connector = TradingBotConnector(
        base_url=settings.STRATEX_URL,
        api_key=settings.STRATEX_API_KEY,
    )
    equity_val: float | None = None
    volatility_metric: float | None = None
    drawdown_metric: float | None = None
    try:
        telemetry_obs = await trading_connector.fetch(start=now - timedelta(hours=48), end=now)
        for obs in telemetry_obs:
            if "volatility" in obs.series_id:
                volatility_metric = float(obs.value)
            elif "drawdown" in obs.series_id:
                drawdown_metric = float(obs.value)
            elif "equity" in obs.series_id:
                equity_val = float(obs.value)
    except Exception as exc:
        logger.warning("stratex_telemetry_unavailable", error=type(exc).__name__)

    if volatility_metric is None or drawdown_metric is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Fresh Stratex volatility and drawdown telemetry are required. "
                "No market forecast was generated from placeholder values."
            ),
        )
    # 3. Compute Volatility Forecast
    sentiment = float(exogenous_adj.get("sentiment_multiplier", 1.0))
    vol_mult = float(exogenous_adj.get("volatility_multiplier", 1.0))
    base_prob = min(0.92, max(0.12, (volatility_metric * 0.70) * vol_mult))
    vol_prob = round(base_prob, 3)
    confidence_score = round(
        max(0.65, min(0.95, 0.84 - exogenous_adj.get("confidence_penalty", 0.0))), 2
    )

    lower_pct = round(-2.5 * vol_mult, 2)
    upper_pct = round((3.5 if sentiment >= 1.0 else 1.8) * vol_mult, 2)

    drivers_list = [f"stratex_volatility_idx:{volatility_metric:.2f}"]
    if equity_val is not None:
        drivers_list.append(f"telemetry_equity_level:${equity_val:.0f}")
    if top_findings:
        drivers_list.append(f"intelx:{top_findings[0][:40]}")
    else:
        drivers_list.append("intelx:market_liquidity_baseline")

    # 4. Compute Drawdown Risk
    base_dd_prob = min(
        0.85, max(0.08, (drawdown_metric / 15.0) * (1.2 if sentiment < 0.9 else 0.85))
    )
    dd_prob = round(base_dd_prob, 3)
    dd_level = "HIGH" if dd_prob > 0.40 else ("MEDIUM" if dd_prob > 0.20 else "LOW")

    # 5. Determine Regime Outlook
    if sentiment > 1.10 and vol_prob < 0.50:
        regime = "TRENDING_BULL"
        predicted_dir = "UPWARD"
        rationale = (
            "IntelX market signals indicate strong spot accumulation and favorable "
            f"liquidity for {clean_sym}."
        )
    elif sentiment < 0.90 or dd_prob > 0.35:
        regime = "TRENDING_BEAR"
        predicted_dir = "DOWNWARD"
        rationale = (
            f"Elevated drawdown pressure ({dd_prob * 100:.1f}%) and conservative market "
            "sentiment require defensive posture."
        )
    elif vol_prob > 0.55:
        regime = "HIGH_VOLATILITY"
        predicted_dir = "VOLATILE"
        rationale = (
            f"IntelX volatility multiplier ({vol_mult:.2f}x) flags expanding price "
            f"distribution for {clean_sym}."
        )
    else:
        regime = "RANGING"
        predicted_dir = "SIDEWAYS"
        rationale = f"{clean_sym} trading in consolidated regime with bounded drawdown risk."

    if top_findings:
        rationale += f" Catalyst: {top_findings[0]}"

    # 6. Qualitative Grounding with Multi-Agent Inference Gateway
    inference_grounded = False
    if include_inference:
        try:
            inf_enhancement = await ecosystem_adapter.enhance_forecast_with_inference(
                metric_name=f"market:crypto:{clean_sym}:volatility_{horizon_hours}h",
                point_estimate=volatility_metric,
                range_lower=lower_pct,
                range_upper=upper_pct,
                model_used="statsforecast_garch_intelx",
                probability=vol_prob,
                contextual_factors=[
                    rationale,
                    *([f"Stratex equity: ${equity_val:.2f}"] if equity_val is not None else []),
                ],
                target_context={"domain": "cryptocurrency_trading", "symbol": clean_sym},
            )
            if inf_enhancement and "consensus" in inf_enhancement:
                consensus_summary = inf_enhancement["consensus"].get("summary", "")[:100]
                rationale += f" [Inference Consensus: {consensus_summary}]"
                inference_grounded = True
        except Exception as exc:
            logger.debug("inference_enhancement_skipped", error=str(exc))

    # 7. Persist to Futuris Database if repository provided
    persisted_forecast: Forecast | None = None
    if forecast_repo:
        try:
            conf_enum = (
                ConfidenceLevel.HIGH
                if confidence_score >= 0.85
                else (ConfidenceLevel.MEDIUM if confidence_score >= 0.70 else ConfidenceLevel.LOW)
            )
            ev_id = uuid4()
            # The numbers come from the Stratex telemetry read above, so the
            # evidence hash is taken over exactly those observations.
            telemetry_evidence = {
                "symbol": clean_sym,
                "as_of": now.isoformat(),
                "volatility": volatility_metric,
                "drawdown": drawdown_metric,
                "equity": equity_val,
                "observations": [o.model_dump(mode="json") for o in telemetry_obs],
            }
            evidence_item = EvidenceRef(
                evidence_id=ev_id,
                source="stratex:telemetry",
                source_trust=SourceTrust.HIGH,
                signal_class=SignalClass.TELEMETRY,
                as_of=now,
                snapshot_path=f"inline://market/{clean_sym}/stratex-telemetry.json",
                content_hash=content_hash_of(telemetry_evidence),
                evidence_class=EvidenceClass.LIVE,
            )
            db_forecast = Forecast(
                forecast_id=forecast_id,
                target=f"market:crypto:{clean_sym}:volatility_{horizon_hours}h",
                as_of=now,
                horizon=timedelta(hours=horizon_hours),
                expires_at=now + timedelta(hours=horizon_hours),
                review_at=now + (timedelta(hours=horizon_hours) / 4),
                prediction=volatility_metric,
                range_lower=lower_pct,
                range_upper=upper_pct,
                probability=vol_prob,
                confidence=conf_enum,
                drivers=[
                    Driver(
                        name=d[:32],
                        direction="positive" if sentiment >= 1.0 else "negative",
                        strength=0.85,
                        leading_or_lagging="leading",
                        evidence_refs=[ev_id],
                    )
                    for d in drivers_list[:3]
                ],
                evidence=[evidence_item],
                assumptions=[rationale],
                model_version="statsforecast:market_volatility@v2",
                status=ForecastStatus.ACTIVE,
                evidence_class=EvidenceClass.LIVE,
                evidence_source="stratex_telemetry",
            )
            await forecast_repo.create(db_forecast)
            await AuditLogger(forecast_repo.session).log_mutation(
                actor_label=actor_label,
                action="create_market_forecast",
                entity="forecast",
                entity_id=str(db_forecast.forecast_id),
                payload={
                    "target": db_forecast.target,
                    "symbol": clean_sym,
                    "evidence_class": db_forecast.evidence_class.value,
                },
            )
            persisted_forecast = db_forecast
            logger.info("market_forecast_persisted", forecast_id=str(forecast_id), symbol=clean_sym)
        except Exception as exc:
            # Do not degrade into a success response: a forecast that could not
            # be written has no provenance, and reporting it as a normal
            # advisory would fabricate a persisted record that does not exist
            # (this path used to return 200 with ``forecast_id: null`` while the
            # request-scoped session was left in "pending rollback" state, which
            # then aborted the connection during teardown).
            logger.warning("market_forecast_persistence_failed", error=str(exc))
            await safe_rollback(forecast_repo.session)
            raise FuturisAPIError(
                code="storage_write_failed",
                message=(
                    "Market forecast could not be persisted, so no advisory was produced. "
                    "Stratex telemetry was fetched but the storage write failed."
                ),
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                details={"entity": "forecast", "error": type(exc).__name__},
            ) from exc

    # 8. Publish only a persisted, evidence-backed forecast. Predictions are advisory and
    # must never grant Stratex or another recipient authority to place a trade.
    memora_event_id = None
    if persisted_forecast is not None:
        try:
            memora_event_id = await publish_forecast_advisory(
                persisted_forecast,
                confidence_score,
            )
        except (MemoraForecastPublishError, ValueError) as exc:
            logger.warning(
                "memora_market_event_publish_failed",
                forecast_id=str(forecast_id),
                error=str(exc)
                if isinstance(exc, MemoraForecastPublishError)
                else type(exc).__name__,
            )

    # 9. Outbound Notify to Stratex
    try:
        await ecosystem_adapter.dispatch_market_forecast_to_stratex(
            symbol=clean_sym,
            forecast_payload={
                "symbol": clean_sym,
                "volatility_probability": vol_prob,
                "regime": regime,
                "predicted_direction": predicted_dir,
                "drawdown_risk": dd_level,
                "forecast_id": str(forecast_id),
            },
        )
    except Exception as exc:
        logger.debug("stratex_outbound_dispatch_skipped", error=str(exc))

    return MarketForecastResponse(
        status="OK",
        symbol=clean_sym,
        volatility_forecast=VolatilityForecastPayload(
            probability=vol_prob,
            confidence=confidence_score,
            horizon_hours=horizon_hours,
            range_pct=[lower_pct, upper_pct],
            expected_volatility=round(volatility_metric, 4),
            drivers=drivers_list,
        ),
        drawdown_risk=DrawdownRiskPayload(
            probability=dd_prob,
            threshold_pct=0.05,
            horizon_hours=horizon_hours,
            risk_level=dd_level,
        ),
        regime_outlook=RegimeOutlookPayload(
            current=regime,
            transition_probability=round(min(0.45, max(0.15, vol_prob * 0.6)), 3),
            predicted_direction=predicted_dir,
            rationale=rationale,
        ),
        intelx_context_included=bool(intelx_reports),
        inference_grounded=inference_grounded,
        forecast_id=str(persisted_forecast.forecast_id if persisted_forecast else forecast_id),
        timestamp=now.isoformat(),
        memora_event_id=memora_event_id,
        evidence_class=(
            persisted_forecast.evidence_class if persisted_forecast else EvidenceClass.LIVE
        ),
        evidence_source=(
            persisted_forecast.evidence_source
            if persisted_forecast
            else "stratex_telemetry"
        ),
    )


@router.post(
    "/forecast",
    response_model=MarketForecastResponse,
    summary="Generate Stratex-Compatible Market Volatility Forecast",
)
async def post_market_forecast(
    user: RequireAnalyst,
    req: MarketForecastRequest | None = Body(None),
    symbol: str | None = Query(None),
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
) -> MarketForecastResponse:
    """Serve a market forecast request from Stratex or ecosystem bots."""
    target_symbol = (req.symbol if req and req.symbol else None) or symbol or "BTCUSDT"
    return await _generate_market_prediction(
        symbol=target_symbol,
        horizon_hours=24,
        include_intelx=req.include_intelx if req else True,
        include_inference=req.include_inference if req else True,
        forecast_repo=forecast_repo,
        actor_label=user.label,
    )


@router.get(
    "/forecast",
    response_model=MarketForecastResponse,
    summary="Query Latest Market Volatility Forecast for Asset",
)
async def get_market_forecast(
    user: AllowAnonymousRead,
    symbol: str = Query("BTCUSDT"),
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
) -> MarketForecastResponse:
    """Compute a fresh calibrated prediction for the specified crypto symbol.

    This is the query-string twin of the POST route: it runs the same pipeline
    (live Stratex telemetry required, no placeholder values) and persists the
    resulting forecast.  It deliberately does *not* serve the last stored row:
    the persisted forecast body holds the numbers but not the advisory payload
    (volatility/drawdown/regime breakdown) that this response model promises, so
    re-serving a stored row would mean inventing that payload.
    """
    _ = user
    return await _generate_market_prediction(
        symbol=symbol,
        horizon_hours=24,
        include_intelx=True,
        include_inference=True,
        forecast_repo=forecast_repo,
        actor_label="anonymous_read",
    )


@router.get(
    "/accuracy",
    response_model=MarketAccuracyResponse,
    summary="Query Historical Accuracy Metrics for Market Forecasts",
)
async def get_market_accuracy(
    user: AllowAnonymousRead,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MarketAccuracyResponse:
    """Empirical accuracy of resolved market forecasts, computed from stored outcomes.

    Returns ``status="insufficient_data"`` with zero counts when no market
    forecast has been resolved yet. Nothing here is estimated or assumed: every
    number is derived from the outcome rows in this database, and the sample
    size is reported alongside so a client can see how thin the evidence is.
    """
    _ = user
    pairs = await OutcomeRepository(session).list_resolved_with_forecasts(target_prefix="market:")

    total = len(pairs)
    if total == 0:
        return MarketAccuracyResponse(
            total_evaluated=0,
            accuracy_pct=0.0,
            brier_score=0.0,
            status="insufficient_data",
            evidence_class=None,
            sample_note="no resolved market forecasts yet",
        )

    contained = 0
    brier_terms: list[float] = []
    recent_records: list[dict[str, Any]] = []
    for forecast, outcome in pairs:
        within_range = forecast.range_lower <= outcome.observed_value <= forecast.range_upper
        contained += int(within_range)
        if forecast.probability is not None:
            observed = 1.0 if outcome.event_occurred else 0.0
            brier_terms.append((forecast.probability - observed) ** 2)
        recent_records.append(
            {
                "symbol": forecast.target.split(":")[2]
                if ":" in forecast.target
                else forecast.target,
                "prediction_correct": within_range,
                "metric": "volatility_range_contained",
                "resolved_at": outcome.resolved_at.isoformat(),
            }
        )

    brier = round(sum(brier_terms) / len(brier_terms), 4) if brier_terms else 0.0
    return MarketAccuracyResponse(
        total_evaluated=total,
        accuracy_pct=round((contained / total) * 100.0, 2),
        brier_score=brier,
        status="ACTIVE",
        evidence_class=EvidenceClass.DERIVED,
        sample_note=f"{len(brier_terms)} of {total} resolved forecasts carried a probability",
        recent_records=recent_records[-20:],
    )
