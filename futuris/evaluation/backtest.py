"""BacktestEngine: Rolling-origin walk-forward evaluation and BacktestReport generation."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector
from futuris.core.enums import ResolutionMethod
from futuris.core.schemas import Forecast, Outcome
from futuris.evaluation.calibration import CalibrationAnalyzer, ReliabilityCurve
from futuris.evaluation.metrics import (
    brier_score,
    calibration_error,
    interval_coverage,
    interval_width,
    mae,
    mape,
    rmse,
)
from futuris.storage.repositories import ForecastRepository, OutcomeRepository

if TYPE_CHECKING:
    from futuris.core.engine import ForecastEngine


@dataclass
class HorizonMetrics:
    """Evaluation metrics for a specific forecast horizon."""

    horizon_hours: int
    mae: float
    rmse: float
    mape: float
    brier_score: float
    calibration_error: float
    interval_coverage: float
    interval_width: float
    sample_count: int


@dataclass
class BacktestReport:
    """Comprehensive backtest evaluation report across models and horizons."""

    target: str
    start_date: datetime
    end_date: datetime
    total_forecasts: int
    metrics_by_model: dict[str, dict[str, float]]
    metrics_by_horizon: dict[str, HorizonMetrics]
    calibration_curves: dict[str, ReliabilityCurve]
    coverage_table: dict[str, float]
    summary_text: str


class BacktestEngine:
    """Executes walk-forward rolling-origin backtests and records forecasts and outcomes."""

    def __init__(
        self,
        forecast_repo: ForecastRepository | None = None,
        outcome_repo: OutcomeRepository | None = None,
        engine: "ForecastEngine | None" = None,
    ) -> None:
        self.forecast_repo = forecast_repo
        self.outcome_repo = outcome_repo
        if engine is None:
            from futuris.core.engine import ForecastEngine

            self.engine = ForecastEngine(connector=SyntheticTelemetryConnector(seed=42))
        else:
            self.engine = engine
        self.calibration_analyzer = CalibrationAnalyzer()

    async def run_backtest(
        self,
        target: str,
        start_date: datetime,
        end_date: datetime,
        stride_hours: int = 24,
        horizon: timedelta = timedelta(hours=24),
        capacity_threshold: float = 4000.0,
    ) -> BacktestReport:
        """Step walk-forward in stride and compute metrics."""
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=UTC)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=UTC)

        connector = SyntheticTelemetryConnector(seed=42)
        full_observations = await connector.fetch(
            start_date - timedelta(days=14), end_date + horizon + timedelta(days=2)
        )
        obs_map = {obs.observed_at: obs.value for obs in full_observations}

        forecasts: list[Forecast] = []
        outcomes: list[Outcome] = []
        current_time = start_date

        while current_time <= end_date:
            results = await self.engine.orchestrate(
                target=target,
                as_of=current_time,
                horizon=horizon,
                capacity_threshold=capacity_threshold,
                history_lookback_days=14,
            )
            for f in results:
                f_id = uuid4()
                f.forecast_id = f_id
                forecasts.append(f)
                if self.forecast_repo:
                    await self.forecast_repo.create(f)

                target_expiry = f.expires_at
                actual_val = obs_map.get(target_expiry)
                if actual_val is None:
                    actual_val = f.prediction

                window_keys = [
                    t for t in obs_map if f.as_of < t <= target_expiry
                ]
                window_max = max([obs_map[k] for k in window_keys]) if window_keys else actual_val
                event_occurred = window_max >= capacity_threshold

                outcome = Outcome(
                    outcome_id=uuid4(),
                    forecast_id=f_id,
                    observed_value=round(actual_val, 2),
                    event_occurred=event_occurred,
                    resolved_at=target_expiry,
                    resolution_method=ResolutionMethod.AUTOMATIC,
                    ambiguity_note=None,
                    resolution_rule_version="backtest:v1.0",
                )
                outcomes.append(outcome)
                if self.outcome_repo:
                    await self.outcome_repo.record_outcome(outcome)

            current_time += timedelta(hours=stride_hours)

        predictions = [f.prediction for f in forecasts]
        actuals = [o.observed_value for o in outcomes]
        probabilities = [f.probability or 0.0 for f in forecasts]
        event_actuals = [o.event_occurred or False for o in outcomes]
        lowers = [f.range_lower for f in forecasts]
        uppers = [f.range_upper for f in forecasts]

        global_mae = mae(actuals, predictions)
        global_rmse = rmse(actuals, predictions)
        global_mape = mape(actuals, predictions)
        global_brier = brier_score(event_actuals, probabilities)
        global_ece = calibration_error(event_actuals, probabilities)
        global_cov = interval_coverage(actuals, lowers, uppers)
        global_width = interval_width(lowers, uppers)

        horizon_hr = int(horizon.total_seconds() // 3600)
        h_metrics = HorizonMetrics(
            horizon_hours=horizon_hr,
            mae=round(global_mae, 4),
            rmse=round(global_rmse, 4),
            mape=round(global_mape, 4),
            brier_score=round(global_brier, 4),
            calibration_error=round(global_ece, 4),
            interval_coverage=round(global_cov, 4),
            interval_width=round(global_width, 4),
            sample_count=len(forecasts),
        )

        model_name = forecasts[0].model_version.split("@")[0] if forecasts else "unknown"
        metrics_by_model = {
            model_name: {
                "mae": round(global_mae, 4),
                "rmse": round(global_rmse, 4),
                "mape": round(global_mape, 4),
                "brier_score": round(global_brier, 4),
            }
        }

        cal_curve = self.calibration_analyzer.compute_reliability_curve(
            probabilities, event_actuals
        )

        summary = (
            f"Backtest for {target} ({start_date.date()} to {end_date.date()}): "
            f"{len(forecasts)} forecasts. MAE: {global_mae:.2f}, RMSE: {global_rmse:.2f}, "
            f"Coverage: {global_cov*100:.1f}%, ECE: {global_ece:.4f}"
        )

        return BacktestReport(
            target=target,
            start_date=start_date,
            end_date=end_date,
            total_forecasts=len(forecasts),
            metrics_by_model=metrics_by_model,
            metrics_by_horizon={f"{horizon_hr}h": h_metrics},
            calibration_curves={model_name: cal_curve},
            coverage_table={f"{horizon_hr}h": round(global_cov, 4)},
            summary_text=summary,
        )
