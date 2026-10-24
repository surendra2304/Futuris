"""
Futuris/predict/forecast_explainability_engine.py — Futuris Forecast Explainability Engine.

Explain forecast drivers, feature importance, prediction decomposition,
and counterfactual explanations.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class FeatureImportance:
    """Importance of a feature in a forecast."""
    feature_name: str
    importance: float = 0.0
    direction: str = "positive"  # positive, negative, neutral
    contribution: float = 0.0


@dataclass
class ForecastExplanation:
    """An explanation for a forecast."""
    explanation_id: str
    forecast_id: str
    summary: str = ""
    drivers: list[FeatureImportance] = field(default_factory=list)
    confidence: float = 0.5
    method: str = "feature_decomposition"
    timestamp: float = field(default_factory=time.time)


@dataclass
class CounterfactualExplanation:
    """A counterfactual explanation."""
    explanation_id: str
    forecast_id: str
    what_changed: str = ""
    original_forecast: float = 0.0
    counterfactual_forecast: float = 0.0
    delta: float = 0.0
    explanation: str = ""


class ForecastExplainabilityEngine:
    """Engine for explaining forecasts."""

    def __init__(self) -> None:
        self._explanations: dict[str, ForecastExplanation] = {}
        self._counterfactuals: dict[str, CounterfactualExplanation] = {}

    def explain_forecast(self, forecast_id: str, features: dict[str, float]) -> ForecastExplanation:
        """Generate an explanation for a forecast."""
        explanation_id = f"exp_{uuid.uuid4().hex[:12]}"
        drivers = []
        for feature_name, value in features.items():
            importance = abs(value) / max(1, sum(abs(v) for v in features.values()))
            direction = "positive" if value > 0 else "negative" if value < 0 else "neutral"
            drivers.append(FeatureImportance(
                feature_name=feature_name, importance=importance,
                direction=direction, contribution=value,
            ))
        drivers.sort(key=lambda d: d.importance, reverse=True)
        top_drivers = ", ".join(d.feature_name for d in drivers[:3])
        explanation = ForecastExplanation(
            explanation_id=explanation_id, forecast_id=forecast_id,
            summary=f"Forecast driven primarily by: {top_drivers}",
            drivers=drivers, confidence=0.8,
        )
        self._explanations[explanation_id] = explanation
        return explanation

    def generate_counterfactual(self, forecast_id: str, original_value: float,
                                counterfactual_value: float, what_changed: str) -> CounterfactualExplanation:
        """Generate a counterfactual explanation."""
        exp_id = f"cf_exp_{uuid.uuid4().hex[:12]}"
        cf = CounterfactualExplanation(
            explanation_id=exp_id, forecast_id=forecast_id,
            what_changed=what_changed,
            original_forecast=original_value,
            counterfactual_forecast=counterfactual_value,
            delta=counterfactual_value - original_value,
            explanation=f"Changing {what_changed} shifts forecast by {counterfactual_value - original_value:.2f}",
        )
        self._counterfactuals[exp_id] = cf
        return cf

    def get_explanation(self, explanation_id: str) -> dict[str, Any]:
        """Get an explanation by ID."""
        exp = self._explanations.get(explanation_id)
        if not exp:
            raise HTTPException(status_code=404, detail="Explanation not found")
        return {
            "explanation_id": exp.explanation_id,
            "forecast_id": exp.forecast_id,
            "summary": exp.summary,
            "drivers": [
                {"feature": d.feature_name, "importance": d.importance, "direction": d.direction}
                for d in exp.drivers[:5]
            ],
            "confidence": exp.confidence,
        }

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_explanations": len(self._explanations),
            "total_counterfactuals": len(self._counterfactuals),
            "engine_status": "operational",
        }


engine = ForecastExplainabilityEngine()


@router.post("/futuris/explain/forecasts")
async def explain_forecast(forecast_id: str, features: dict[str, float]):
    """Generate an explanation for a forecast."""
    explanation = engine.explain_forecast(forecast_id, features)
    return {
        "explanation_id": explanation.explanation_id,
        "summary": explanation.summary,
        "top_drivers": [
            {"feature": d.feature_name, "importance": d.importance}
            for d in explanation.drivers[:3]
        ],
        "confidence": explanation.confidence,
    }


@router.post("/futuris/explain/counterfactuals")
async def generate_counterfactual(forecast_id: str, original_value: float,
                                  counterfactual_value: float, what_changed: str):
    """Generate a counterfactual explanation."""
    cf = engine.generate_counterfactual(forecast_id, original_value, counterfactual_value, what_changed)
    return {
        "explanation_id": cf.explanation_id,
        "delta": cf.delta,
        "explanation": cf.explanation,
    }


@router.get("/futuris/explain/explanations/{explanation_id}")
async def get_explanation(explanation_id: str):
    """Get an explanation."""
    return engine.get_explanation(explanation_id)


@router.get("/futuris/explain/report")
async def explain_report():
    """Get explainability report."""
    return engine.get_report()
