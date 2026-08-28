"""Model adapters, baseline forecasters, probabilistic models, and ensemble routing."""

from futuris.models.adapters import (
    AutoARIMAAdapter,
    AutoETSAdapter,
    DriftAdapter,
    MeanEnsembleAdapter,
    NaiveAdapter,
    SeasonalNaiveAdapter,
)
from futuris.models.base import ModelAdapter, ModelPrediction, PredictionIntervals
from futuris.models.registry import ModelRegistry, model_registry
from futuris.models.routing import ModelRouter, SeriesMetadata

__all__ = [
    "AutoARIMAAdapter",
    "AutoETSAdapter",
    "DriftAdapter",
    "MeanEnsembleAdapter",
    "ModelAdapter",
    "ModelPrediction",
    "ModelRegistry",
    "ModelRouter",
    "NaiveAdapter",
    "PredictionIntervals",
    "SeasonalNaiveAdapter",
    "SeriesMetadata",
    "model_registry",
]
