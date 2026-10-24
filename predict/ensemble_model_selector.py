"""
Futuris/predict/ensemble_model_selector.py — Futuris Ensemble Model Selector.

Dynamic model selection based on forecast characteristics, model
performance tracking, and ensemble composition optimization.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class ModelMetric:
    """Performance metrics for a model."""
    model_name: str
    brier_score: float = 0.0
    calibration: float = 0.5
    accuracy: float = 0.5
    avg_latency_ms: float = 0.0
    total_forecasts: int = 0


@dataclass
class ModelSelection:
    """A model selection decision."""
    selection_id: str
    forecast_type: str
    selected_models: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    confidence: float = 0.5


@dataclass
class EnsembleComposition:
    """An ensemble model composition."""
    composition_id: str
    name: str
    models: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    expected_performance: float = 0.0
    created_at: float = field(default_factory=time.time)


class EnsembleModelSelector:
    """Engine for dynamic ensemble model selection."""

    def __init__(self) -> None:
        self._metrics: dict[str, ModelMetric] = {}
        self._compositions: dict[str, EnsembleComposition] = {}
        self._selections: list[ModelSelection] = []

    def register_model(self, model_name: str, brier_score: float = 0.0,
                       calibration: float = 0.5, accuracy: float = 0.5) -> ModelMetric:
        """Register a model with its metrics."""
        metric = ModelMetric(
            model_name=model_name, brier_score=brier_score,
            calibration=calibration, accuracy=accuracy,
            total_forecasts=1,
        )
        self._metrics[model_name] = metric
        return metric

    def update_metrics(self, model_name: str, brier_score: float,
                       calibration: float, accuracy: float) -> bool:
        """Update model metrics."""
        metric = self._metrics.get(model_name)
        if not metric:
            return False
        metric.brier_score = brier_score
        metric.calibration = calibration
        metric.accuracy = accuracy
        metric.total_forecasts += 1
        return True

    def select_models(self, forecast_type: str, num_models: int = 3) -> ModelSelection:
        """Select models for a forecast type."""
        selection_id = f"sel_{uuid.uuid4().hex[:12]}"
        sorted_models = sorted(
            self._metrics.values(),
            key=lambda m: (m.accuracy * 0.4 + m.calibration * 0.3 + (1 - m.brier_score) * 0.3),
            reverse=True,
        )
        selected = sorted_models[:num_models]
        weights = {}
        total_score = sum(
            m.accuracy * 0.4 + m.calibration * 0.3 + (1 - m.brier_score) * 0.3
            for m in selected
        )
        for m in selected:
            weights[m.model_name] = (
                (m.accuracy * 0.4 + m.calibration * 0.3 + (1 - m.brier_score) * 0.3) / max(1, total_score)
            )
        selection = ModelSelection(
            selection_id=selection_id, forecast_type=forecast_type,
            selected_models=[m.model_name for m in selected],
            weights=weights,
            reason=f"Selected top {num_models} models based on accuracy, calibration, and Brier score",
            confidence=sum(weights.values()) / max(1, len(weights)),
        )
        self._selections.append(selection)
        return selection

    def create_ensemble(self, name: str, model_names: list[str]) -> EnsembleComposition:
        """Create an ensemble composition."""
        composition_id = f"ens_{uuid.uuid4().hex[:12]}"
        weights = {}
        scores = [
            self._metrics[m].accuracy * 0.4 + self._metrics[m].calibration * 0.3
            for m in model_names if m in self._metrics
        ]
        total_score = sum(scores)
        for m, s in zip(model_names, scores):
            weights[m] = s / max(1, total_score)
        composition = EnsembleComposition(
            composition_id=composition_id, name=name,
            models=model_names, weights=weights,
            expected_performance=sum(scores) / max(1, len(scores)),
        )
        self._compositions[composition_id] = composition
        return composition

    def get_compositions(self) -> list[dict[str, Any]]:
        """Get all ensemble compositions."""
        return [
            {
                "composition_id": c.composition_id,
                "name": c.name,
                "models": c.models,
                "expected_performance": c.expected_performance,
            }
            for c in self._compositions.values()
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_models": len(self._metrics),
            "total_compositions": len(self._compositions),
            "total_selections": len(self._selections),
            "engine_status": "operational",
        }


engine = EnsembleModelSelector()


@router.post("/futuris/ensemble/models")
async def register_model(model_name: str, brier_score: float = 0.0, calibration: float = 0.5, accuracy: float = 0.5):
    """Register a model."""
    metric = engine.register_model(model_name, brier_score, calibration, accuracy)
    return {"model_name": metric.model_name, "status": "registered"}


@router.post("/futuris/ensemble/select")
async def select_models(forecast_type: str, num_models: int = 3):
    """Select models for a forecast."""
    selection = engine.select_models(forecast_type, num_models)
    return {
        "selected_models": selection.selected_models,
        "weights": selection.weights,
        "reason": selection.reason,
    }


@router.post("/futuris/ensemble/compositions")
async def create_ensemble(name: str, model_names: list[str]):
    """Create an ensemble composition."""
    composition = engine.create_ensemble(name, model_names)
    return {"composition_id": composition.composition_id, "models": composition.models}


@router.get("/futuris/ensemble/compositions")
async def get_compositions():
    """Get ensemble compositions."""
    return {"compositions": engine.get_compositions()}


@router.get("/futuris/ensemble/report")
async def ensemble_report():
    """Get ensemble selector report."""
    return engine.get_report()
