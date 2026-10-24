"""
Futuris/futuris/services/quantum_forecast_engine.py — Futuris Quantum Forecast Engine.

Quantum-inspired forecasting, superposition of scenarios, probabilistic
amplitude amplification for forecast refinement, and quantum annealing
for optimization.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class QuantumState:
    """A quantum-inspired forecast state."""
    state_id: str
    scenario_name: str
    amplitude: float = 0.0  # probability amplitude (0 to 1)
    phase: float = 0.0  # quantum phase
    description: str = ""


@dataclass
class SuperpositionForecast:
    """A superposition of forecast scenarios."""
    forecast_id: str
    outcomes: list[QuantumState] = field(default_factory=list)
    collapsed_outcome: str = ""
    collapse_probability: float = 0.0
    created_at: float = field(default_factory=time.time)


@dataclass
class AmplificationResult:
    """Result of amplitude amplification."""
    result_id: str
    forecast_id: str
    amplification_factor: float = 1.0
    refined_confidence: float = 0.0
    iterations: int = 0
    improvement: float = 0.0


class QuantumForecastEngine:
    """Engine for quantum-inspired forecasting."""

    def __init__(self) -> None:
        self._forecasts: dict[str, SuperpositionForecast] = {}
        self._results: list[AmplificationResult] = []

    def create_superposition(self, scenario_names: list[str],
                              amplitudes: list[float] | None = None,
                              descriptions: list[str] | None = None) -> SuperpositionForecast:
        """Create a superposition of forecast scenarios."""
        forecast_id = f"qf_{uuid.uuid4().hex[:12]}"
        forecast = SuperpositionForecast(forecast_id=forecast_id)
        total_amp = 0.0

        for i, name in enumerate(scenario_names):
            amp = amplitudes[i] if amplitudes and i < len(amplitudes) else 1.0 / len(scenario_names)
            desc = descriptions[i] if descriptions and i < len(descriptions) else ""
            state = QuantumState(
                state_id=f"st_{uuid.uuid4().hex[:8]}",
                scenario_name=name, amplitude=amp,
                phase=float(i) * 0.5, description=desc,
            )
            forecast.outcomes.append(state)
            total_amp += amp

        # Normalize amplitudes
        if total_amp > 0:
            for state in forecast.outcomes:
                state.amplitude /= total_amp

        self._forecasts[forecast_id] = forecast
        return forecast

    def collapse(self, forecast_id: str) -> SuperpositionForecast:
        """Collapse the superposition to a single outcome."""
        forecast = self._forecasts.get(forecast_id)
        if not forecast:
            raise HTTPException(status_code=404, detail="Forecast not found")

        # Pick outcome with highest amplitude
        best = max(forecast.outcomes, key=lambda s: s.amplitude)
        forecast.collapsed_outcome = best.scenario_name
        forecast.collapse_probability = best.amplitude
        return forecast

    def amplify(self, forecast_id: str, iterations: int = 1,
                target_outcome: str = "") -> AmplificationResult:
        """Apply amplitude amplification to refine forecast."""
        forecast = self._forecasts.get(forecast_id)
        if not forecast:
            raise HTTPException(status_code=404, detail="Forecast not found")

        result = AmplificationResult(
            result_id=f"amp_{uuid.uuid4().hex[:12]}",
            forecast_id=forecast_id, iterations=iterations,
        )

        if target_outcome:
            target = next((s for s in forecast.outcomes if s.scenario_name == target_outcome), None)
            if target:
                initial_amp = target.amplitude
                result.amplification_factor = (1 + iterations) ** 0.5
                target.amplitude = min(1.0, target.amplitude * result.amplification_factor)
                # Renormalize
                total = sum(s.amplitude for s in forecast.outcomes)
                if total > 0:
                    for s in forecast.outcomes:
                        s.amplitude /= total
                result.refined_confidence = target.amplitude
                result.improvement = result.refined_confidence - initial_amp
        else:
            result.amplification_factor = (1 + iterations) ** 0.5
            result.refined_confidence = sum(s.amplitude ** 2 for s in forecast.outcomes)

        self._results.append(result)
        return result

    def get_forecast(self, forecast_id: str) -> dict[str, Any]:
        """Get a forecast."""
        forecast = self._forecasts.get(forecast_id)
        if not forecast:
            raise HTTPException(status_code=404, detail="Forecast not found")
        return {
            "forecast_id": forecast.forecast_id,
            "outcome_count": len(forecast.outcomes),
            "collapsed": forecast.collapsed_outcome,
            "collapse_probability": forecast.collapse_probability,
            "outcomes": [
                {"name": s.scenario_name, "amplitude": round(s.amplitude, 4)}
                for s in forecast.outcomes[:5]
            ],
        }

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_forecasts": len(self._forecasts),
            "total_amplifications": len(self._results),
            "avg_amplification": round(
                sum(r.amplification_factor for r in self._results) / max(1, len(self._results)), 2
            ) if self._results else 0,
            "engine_status": "operational",
        }


engine = QuantumForecastEngine()


@router.post("/futuris/quantum/superposition")
async def create_superposition(scenario_names: list[str], amplitudes: list[float] | None = None,
                               descriptions: list[str] | None = None):
    """Create a superposition of forecast scenarios."""
    forecast = engine.create_superposition(scenario_names, amplitudes, descriptions)
    return {"forecast_id": forecast.forecast_id, "outcome_count": len(forecast.outcomes)}


@router.post("/futuris/quantum/collapse/{forecast_id}")
async def collapse(forecast_id: str):
    """Collapse the superposition."""
    forecast = engine.collapse(forecast_id)
    return {"forecast_id": forecast_id, "collapsed_outcome": forecast.collapsed_outcome,
            "probability": forecast.collapse_probability}


@router.post("/futuris/quantum/amplify/{forecast_id}")
async def amplify(forecast_id: str, iterations: int = 1, target_outcome: str = ""):
    """Apply amplitude amplification."""
    result = engine.amplify(forecast_id, iterations, target_outcome)
    return {
        "forecast_id": forecast_id,
        "amplification_factor": result.amplification_factor,
        "refined_confidence": result.refined_confidence,
        "improvement": result.improvement,
    }


@router.get("/futuris/quantum/forecast/{forecast_id}")
async def get_forecast(forecast_id: str):
    """Get a forecast."""
    return engine.get_forecast(forecast_id)


@router.get("/futuris/quantum/report")
async def quantum_report():
    """Get quantum forecast engine report."""
    return engine.get_report()
