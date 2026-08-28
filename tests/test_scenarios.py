"""Tests for DependencyGraph sensitivity arithmetic, Monte Carlo, and immutability."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
import pytest

from futuris.core.enums import ConfidenceLevel, ForecastStatus, SignalClass, SourceTrust
from futuris.core.schemas import Driver, EvidenceRef, Forecast
from futuris.scenarios.engine import ScenarioEngine
from futuris.scenarios.graph import DependencyGraph, Edge
from futuris.scenarios.spec import ScenarioSpec


def test_linear_propagation_hand_computed_3_node_graph():
    """Verify linear sensitivity arithmetic on a hand-computed 3-node graph: A -> B -> C."""
    nodes = ["A", "B", "C"]
    edges = [
        Edge(source="A", target="B", sensitivity=1.5),
        Edge(source="B", target="C", sensitivity=2.0),
    ]
    base_values = {"A": 100.0, "B": 200.0, "C": 300.0}
    graph = DependencyGraph(nodes=nodes, edges=edges, base_values=base_values)

    # Override A by +10% (multiplier = 1.10)
    # delta_A = 0.10
    # delta_B = 0.10 * 1.5 = 0.15 -> B = 200 * 1.15 = 230
    # delta_C = 0.15 * 2.0 = 0.30 -> C = 300 * 1.30 = 390
    results = graph.propagate_linear({"A": 1.10})
    assert np.isclose(results["A"], 110.0)
    assert np.isclose(results["B"], 230.0)
    assert np.isclose(results["C"], 390.0)


def test_monte_carlo_mode_converges_to_known_means():
    """Verify Monte Carlo mode converges to expected mean values with low variance."""
    nodes = ["demand", "revenue"]
    edges = [Edge(source="demand", target="revenue", sensitivity=1.0)]
    base_values = {"demand": 1000.0, "revenue": 500.0}
    graph = DependencyGraph(nodes=nodes, edges=edges, base_values=base_values)

    mc_results = graph.propagate_monte_carlo(
        overrides={"demand": 1.20},
        num_samples=2000,
        sigma_scale=0.02,
        seed=42,
    )
    # Expected demand mean ~ 1200, revenue mean ~ 600
    assert np.isclose(mc_results["demand"]["mean"], 1200.0, atol=20.0)
    assert np.isclose(mc_results["revenue"]["mean"], 600.0, atol=10.0)


@pytest.mark.asyncio
async def test_scenario_engine_immutability_and_comparison_ranking():
    """Verify ScenarioEngine preserves base forecast immutability and ranks divergences."""
    as_of = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    evidence_id = uuid4()
    base_forecast = Forecast(
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

    original_pred = base_forecast.prediction
    original_prob = base_forecast.probability
    original_status = base_forecast.status

    engine = ScenarioEngine()

    spec_base = ScenarioSpec.baseline()
    spec_upside = ScenarioSpec.upside(demand_multiplier=1.20)
    spec_downside = ScenarioSpec.downside(demand_multiplier=0.80)
    spec_stress = ScenarioSpec.stress(demand_multiplier=1.40, capacity_multiplier=0.80)

    res_base = await engine.run_scenario(base_forecast, spec_base)
    res_up = await engine.run_scenario(base_forecast, spec_upside)
    res_down = await engine.run_scenario(base_forecast, spec_downside)
    res_stress = await engine.run_scenario(base_forecast, spec_stress)

    # 1. Assert Strict Immutability
    assert base_forecast.prediction == original_pred
    assert base_forecast.probability == original_prob
    assert base_forecast.status == original_status

    # 2. Assert Coherent Multi-Scenario Results
    d_down = res_down.perturbed_values["demand"]
    d_base = res_base.perturbed_values["demand"]
    d_up = res_up.perturbed_values["demand"]
    d_stress = res_stress.perturbed_values["demand"]
    assert d_down < d_base < d_up < d_stress

    # 3. Compare Scenarios
    comparison = engine.compare(base_forecast, [res_base, res_up, res_down, res_stress])
    assert len(comparison.scenario_names) == 4
    assert len(comparison.divergence_ranking) > 0
    assert "demand" in [d[0] for d in comparison.divergence_ranking]
    assert len(comparison.top_drivers) > 0
