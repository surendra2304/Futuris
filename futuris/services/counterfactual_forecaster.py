"""
Futuris/futuris/services/counterfactual_forecaster.py — Futuris Counterfactual Forecaster.

Counterfactual analysis, alternative scenario modeling, what-if analysis,
and causal impact estimation for forecasting.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class CausalFactor:
    """A causal factor in a forecast."""
    factor_id: str
    name: str
    impact: float = 0.0
    direction: str = "positive"  # positive, negative, neutral
    confidence: float = 0.5
    description: str = ""


@dataclass
class CounterfactualScenario:
    """A counterfactual scenario."""
    scenario_id: str
    description: str
    base_forecast: float = 0.0
    counterfactual_forecast: float = 0.0
    difference: float = 0.0
    factors: list[CausalFactor] = field(default_factory=list)
    probability: float = 0.0
    created_at: float = field(default_factory=time.time)


@dataclass
class WhatIfResult:
    """A what-if analysis result."""
    result_id: str
    question: str
    baseline: float = 0.0
    modified: float = 0.0
    sensitivity: float = 0.0
    interpretation: str = ""


class CounterfactualForecaster:
    """Engine for counterfactual analysis and what-if forecasting."""

    def __init__(self) -> None:
        self._scenarios: list[CounterfactualScenario] = []
        self._whatifs: list[WhatIfResult] = []

    def define_factor(self, name: str, impact: float = 0.0,
                      direction: str = "positive", confidence: float = 0.5,
                      description: str = "") -> CausalFactor:
        """Define a causal factor."""
        factor = CausalFactor(
            factor_id=f"fac_{uuid.uuid4().hex[:8]}", name=name,
            impact=impact, direction=direction,
            confidence=confidence, description=description,
        )
        return factor

    def create_counterfactual(self, description: str, base_forecast: float,
                              factors: list[CausalFactor] = None,
                              probability: float = 0.0) -> CounterfactualScenario:
        """Create a counterfactual scenario."""
        scenario_id = f"cf_{uuid.uuid4().hex[:12]}"
        scenario = CounterfactualScenario(
            scenario_id=scenario_id, description=description,
            base_forecast=base_forecast, probability=probability,
            factors=factors or [],
        )
        # Calculate counterfactual forecast
        adjustment = sum(
            f.impact * f.confidence for f in scenario.factors
            if f.direction == "positive"
        ) - sum(
            f.impact * f.confidence for f in scenario.factors
            if f.direction == "negative"
        )
        scenario.counterfactual_forecast = base_forecast + adjustment
        scenario.difference = scenario.counterfactual_forecast - scenario.base_forecast

        self._scenarios.append(scenario)
        return scenario

    def what_if(self, question: str, baseline: float, modified: float) -> WhatIfResult:
        """Perform a what-if analysis."""
        result_id = f"wi_{uuid.uuid4().hex[:12]}"
        result = WhatIfResult(
            result_id=result_id, question=question,
            baseline=baseline, modified=modified,
        )
        result.sensitivity = abs(modified - baseline) / max(0.01, baseline)
        if result.sensitivity > 0.5:
            result.interpretation = "High sensitivity — small changes lead to large forecast differences"
        elif result.sensitivity > 0.2:
            result.interpretation = "Moderate sensitivity"
        else:
            result.interpretation = "Low sensitivity — forecast is robust to changes"

        self._whatifs.append(result)
        return result

    def get_scenarios(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get counterfactual scenarios."""
        return [
            {
                "description": s.description[:80],
                "base_forecast": s.base_forecast,
                "counterfactual": s.counterfactual_forecast,
                "difference": s.difference,
                "factor_count": len(s.factors),
            }
            for s in self._scenarios[-limit:]
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_scenarios": len(self._scenarios),
            "total_whatifs": len(self._whatifs),
            "avg_difference": round(
                sum(s.difference for s in self._scenarios) / max(1, len(self._scenarios)), 2
            ) if self._scenarios else 0,
            "engine_status": "operational",
        }


engine = CounterfactualForecaster()


@router.post("/futuris/counterfactual/factors")
async def define_factor(name: str, impact: float = 0.0, direction: str = "positive",
                        confidence: float = 0.5, description: str = ""):
    """Define a causal factor."""
    factor = engine.define_factor(name, impact, direction, confidence, description)
    return {"factor_id": factor.factor_id, "impact": factor.impact}


@router.post("/futuris/counterfactual/scenarios")
async def create_counterfactual(description: str, base_forecast: float,
                                factors: list[dict] | None = None, probability: float = 0.0):
    """Create a counterfactual scenario."""
    cf_factors = [engine.define_factor(f.get("name", ""), f.get("impact", 0),
                                       f.get("direction", "positive"),
                                       f.get("confidence", 0.5)) for f in (factors or [])]
    scenario = engine.create_counterfactual(description, base_forecast, cf_factors, probability)
    return {
        "scenario_id": scenario.scenario_id,
        "base_forecast": scenario.base_forecast,
        "counterfactual": scenario.counterfactual_forecast,
        "difference": scenario.difference,
    }


@router.post("/futuris/counterfactual/whatif")
async def what_if(question: str, baseline: float, modified: float):
    """Perform a what-if analysis."""
    result = engine.what_if(question, baseline, modified)
    return {
        "question": question,
        "baseline": result.baseline,
        "modified": result.modified,
        "sensitivity": round(result.sensitivity, 2),
        "interpretation": result.interpretation,
    }


@router.get("/futuris/counterfactual/scenarios")
async def get_scenarios(limit: int = 10):
    """Get counterfactual scenarios."""
    return {"scenarios": engine.get_scenarios(limit)}


@router.get("/futuris/counterfactual/report")
async def counterfactual_report():
    """Get counterfactual forecaster report."""
    return engine.get_report()
