"""Model registry for registered adapters and deterministic version resolution."""

from typing import Any

from futuris.models.adapters import (
    AutoARIMAAdapter,
    AutoETSAdapter,
    DriftAdapter,
    MeanEnsembleAdapter,
    NaiveAdapter,
    SeasonalNaiveAdapter,
)
from futuris.models.base import ModelAdapter


class ModelRegistry:
    """Registry maintaining active and registered ModelAdapter constructors."""

    def __init__(self) -> None:
        self._adapters: dict[str, type[ModelAdapter]] = {
            "naive": NaiveAdapter,
            "seasonal_naive": SeasonalNaiveAdapter,
            "drift": DriftAdapter,
            "auto_ets": AutoETSAdapter,
            "auto_arima": AutoARIMAAdapter,
            "mean_ensemble": MeanEnsembleAdapter,
        }

    def get_adapter(
        self,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> ModelAdapter:
        """Instantiate an adapter instance by name and configuration."""
        if name not in self._adapters:
            msg = f"Unknown model adapter '{name}'. Available: {list(self._adapters.keys())}"
            raise ValueError(msg)
        cls = self._adapters[name]
        return cls(config=config) if config else cls()

    def get_version_string(self, adapter: ModelAdapter) -> str:
        """Compute canonical model_version string (e.g. 'seasonal_naive@v1:c8f93a')."""
        name = adapter.name
        cfg_hash = adapter.get_config_hash()
        return f"{name}@v1:{cfg_hash}"

    def current_active(self) -> list[str]:
        """Return default active adapter names in prioritized order."""
        return ["mean_ensemble", "auto_ets", "seasonal_naive", "drift", "naive"]


model_registry = ModelRegistry()
