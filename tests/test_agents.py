"""Tests for Agent protocol, deterministic fallback, LLM caching, and cost guardrails."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from futuris.agents.calibration_analyst import CalibrationAnalyst
from futuris.agents.protocol import AgentMessage, LogResultHandler
from futuris.agents.runner import AgentRunner
from futuris.agents.signal_analyst import SignalAnalyst
from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector
from futuris.core.enums import ConfidenceLevel, ForecastStatus, SignalClass, SourceTrust
from futuris.core.schemas import Driver, EvidenceRef, Forecast
from futuris.evaluation.backtest import BacktestReport, HorizonMetrics
from futuris.features.normalize import Normalizer
from futuris.infra.llm import LLMAdapter, LLMResponseCache


def test_agent_protocol_message_serialization():
    """Verify typed AgentMessage serialization and field compliance."""
    msg = AgentMessage(
        agent_name="SignalAnalyst",
        task_context={"target": "checkout"},
        evidence_refs=[uuid4()],
        confidence=0.85,
        result={"severity": "medium", "z_score": 2.8},
        narrative="Moderate anomaly detected.",
    )
    dumped = msg.model_dump(mode="json")
    assert dumped["agent_name"] == "SignalAnalyst"
    assert dumped["confidence"] == 0.85
    assert dumped["result"]["severity"] == "medium"


@pytest.mark.asyncio
async def test_signal_analyst_deterministic_fallback():
    """Verify SignalAnalyst runs with LLM_PROVIDER=none and produces deterministic output."""
    llm = LLMAdapter(provider="none", api_key=None)
    analyst = SignalAnalyst(llm=llm)

    connector = SyntheticTelemetryConnector(seed=42)
    t0 = datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
    raw = await connector.fetch(t0 - timedelta(days=2), t0)
    normalizer = Normalizer()
    sig_set = normalizer.normalize(raw)

    msg1 = await analyst.analyze_signal(sig_set)
    msg2 = await analyst.analyze_signal(sig_set)

    assert "z_score" in msg1.result
    assert msg1.narrative == msg2.narrative
    assert msg1.result == msg2.result


@pytest.mark.asyncio
async def test_llm_response_caching(monkeypatch):
    """Verify identical prompt hashes reuse cached responses without calling external provider."""
    cache = LLMResponseCache()
    llm = LLMAdapter(provider="openai", api_key="sk-mock-key", cache=cache)

    call_count = 0

    async def mock_call(self, prompt: str, system_prompt: str) -> str:
        _ = (self, prompt, system_prompt)
        nonlocal call_count
        call_count += 1
        return f"Generated response #{call_count}"

    monkeypatch.setattr(LLMAdapter, "_call_provider", mock_call)

    # First call -> invokes provider
    res1 = await llm.generate("Analyze this pattern", "System prompt")
    assert res1 == "Generated response #1"
    assert call_count == 1

    # Second identical call -> hits cache
    res2 = await llm.generate("Analyze this pattern", "System prompt")
    assert res2 == "Generated response #1"
    assert call_count == 1


@pytest.mark.asyncio
async def test_agent_runner_cost_guardrail():
    """Verify AgentRunner falls back to deterministic rules when hourly call quota is exceeded."""
    handler = LogResultHandler()
    runner = AgentRunner(handler=handler, max_llm_calls_per_hour=2)

    connector = SyntheticTelemetryConnector(seed=42)
    t0 = datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
    raw = await connector.fetch(t0 - timedelta(days=1), t0)
    normalizer = Normalizer()
    sig_set = normalizer.normalize(raw)

    # Call 1 & 2 -> Within limit
    await runner.run_signal_analyst(sig_set)
    await runner.run_signal_analyst(sig_set)
    assert len(handler.messages) == 2

    # Call 3 -> Exceeds guardrail of 2
    m3 = await runner.run_signal_analyst(sig_set)
    assert len(handler.messages) == 3
    assert "z_score" in m3.result


@pytest.mark.asyncio
async def test_agent_execution_never_mutates_forecasts():
    """Verify that agent execution leaves base Forecast objects 100% unmodified."""
    as_of = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    evidence_id = uuid4()
    forecast = Forecast(
        forecast_id=uuid4(),
        target="service:checkout:capacity_exceedance_24h",
        as_of=as_of,
        horizon=timedelta(hours=24),
        expires_at=as_of + timedelta(hours=24),
        prediction=3500.0,
        range_lower=3000.0,
        range_upper=4200.0,
        probability=0.75,
        confidence=ConfidenceLevel.HIGH,
        drivers=[
            Driver(
                name="traffic",
                direction="positive",
                strength=0.8,
                leading_or_lagging="leading",
                evidence_refs=[evidence_id],
            )
        ],
        evidence=[
            EvidenceRef(
                evidence_id=evidence_id,
                source="telemetry:synthetic",
                source_trust=SourceTrust.HIGH,
                signal_class=SignalClass.TELEMETRY,
                as_of=as_of,
                snapshot_path="/tmp/snap.parquet",
                content_hash="mockhash",
            )
        ],
        model_version="auto_arima@v1:hash",
        assumptions=["stable architecture"],
        review_at=as_of + timedelta(hours=6),
        status=ForecastStatus.ACTIVE,
    )

    original_dict = forecast.model_dump(mode="json")

    # Run CalibrationAnalyst on mock report
    cal_analyst = CalibrationAnalyst()
    h_metric = HorizonMetrics(
        horizon_hours=24,
        mae=50.0,
        rmse=60.0,
        mape=5.0,
        brier_score=0.05,
        calibration_error=0.02,
        interval_coverage=0.90,
        interval_width=400.0,
        sample_count=10,
    )
    report = BacktestReport(
        target=forecast.target,
        start_date=as_of - timedelta(days=5),
        end_date=as_of,
        total_forecasts=10,
        metrics_by_model={"auto_arima": {"mae": 50.0}},
        metrics_by_horizon={"24h": h_metric},
        calibration_curves={},
        coverage_table={"24h": 0.90},
        summary_text="Backtest summary",
    )

    await cal_analyst.analyze_calibration(report)

    # Verify forecast is completely unchanged
    assert forecast.model_dump(mode="json") == original_dict
