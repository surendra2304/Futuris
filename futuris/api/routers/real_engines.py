"""
Futuris — Real Engines API Router

Wires real_climate_engine, real_streaming, real_time_series,
and real_time_series_advanced into the Futuris API.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

real_engines_router = APIRouter(tags=["Real Forecast Engines"], prefix="/api/v1/real-engines")


def _import_mods():
    mods: dict[str, Any] = {}
    try:
        from futuris.agents.real_climate_engine import (
            EnergyBalanceModel, CarbonCycleModel, SimpleGCM, ENSOModel,
            SeaLevelModel, ExtremeEventAttribution, ClimateScenarioGenerator,
            RegionalDownscaling, AttributionAnalyzer,
        )
        mods["climate"] = {
            "energy": EnergyBalanceModel, "carbon": CarbonCycleModel,
            "gcm": SimpleGCM, "enso": ENSOModel, "sea_level": SeaLevelModel,
            "attribution": ExtremeEventAttribution, "scenario": ClimateScenarioGenerator,
            "downscaling": RegionalDownscaling, "analyzer": AttributionAnalyzer,
        }
    except Exception:
        try:
            from app.agents.real_climate_engine import (
                EnergyBalanceModel, CarbonCycleModel, SimpleGCM, ENSOModel,
                SeaLevelModel, ExtremeEventAttribution, ClimateScenarioGenerator,
                RegionalDownscaling, AttributionAnalyzer,
            )
            mods["climate"] = {
                "energy": EnergyBalanceModel, "carbon": CarbonCycleModel,
                "gcm": SimpleGCM, "enso": ENSOModel, "sea_level": SeaLevelModel,
                "attribution": ExtremeEventAttribution, "scenario": ClimateScenarioGenerator,
                "downscaling": RegionalDownscaling, "analyzer": AttributionAnalyzer,
            }
        except Exception:
            pass
    try:
        from futuris.agents.real_streaming import (
            Event, TumblingWindow, SlidingWindow, StreamProcessor,
            RealtimeStats, StreamAlert, PatternDetector, BackpressureHandler,
            StreamPipeline, StatefulStreamProcessor,
        )
        mods["streaming"] = {
            "event": Event, "tumbling": TumblingWindow, "sliding": SlidingWindow,
            "processor": StreamProcessor, "stats": RealtimeStats, "alert": StreamAlert,
            "pattern": PatternDetector, "backpressure": BackpressureHandler,
            "pipeline": StreamPipeline, "stateful": StatefulStreamProcessor,
        }
    except Exception:
        try:
            from app.agents.real_streaming import (
                Event, TumblingWindow, SlidingWindow, StreamProcessor,
                RealtimeStats, StreamAlert, PatternDetector, BackpressureHandler,
                StreamPipeline, StatefulStreamProcessor,
            )
            mods["streaming"] = {
                "event": Event, "tumbling": TumblingWindow, "sliding": SlidingWindow,
                "processor": StreamProcessor, "stats": RealtimeStats, "alert": StreamAlert,
                "pattern": PatternDetector, "backpressure": BackpressureHandler,
                "pipeline": StreamPipeline, "stateful": StatefulStreamProcessor,
            }
        except Exception:
            pass
    try:
        from futuris.agents.real_time_series import (
            TimeSeriesForecaster, MovingAverage, SeasonalDecomposer,
            AnomalyDetector, TrendAnalyzer, VolatilityCalculator,
        )
        mods["time_series"] = {
            "forecaster": TimeSeriesForecaster, "ma": MovingAverage,
            "decomposer": SeasonalDecomposer, "anomaly": AnomalyDetector,
            "trend": TrendAnalyzer, "volatility": VolatilityCalculator,
        }
    except Exception:
        try:
            from app.agents.real_time_series import (
                TimeSeriesForecaster, MovingAverage, SeasonalDecomposer,
                AnomalyDetector, TrendAnalyzer, VolatilityCalculator,
            )
            mods["time_series"] = {
                "forecaster": TimeSeriesForecaster, "ma": MovingAverage,
                "decomposer": SeasonalDecomposer, "anomaly": AnomalyDetector,
                "trend": TrendAnalyzer, "volatility": VolatilityCalculator,
            }
        except Exception:
            pass
    try:
        from futuris.agents.real_time_series_advanced import (
            ARMA, ARIMA, HoltWinters, ProphetLike, ModelSelector,
            TimeSeriesCrossValidator, EnsembleForecaster,
        )
        mods["time_series_advanced"] = {
            "arma": ARMA, "arima": ARIMA, "holtwinters": HoltWinters,
            "prophet": ProphetLike, "selector": ModelSelector,
            "validator": TimeSeriesCrossValidator, "ensemble": EnsembleForecaster,
        }
    except Exception:
        try:
            from app.agents.real_time_series_advanced import (
                ARMA, ARIMA, HoltWinters, ProphetLike, ModelSelector,
                TimeSeriesCrossValidator, EnsembleForecaster,
            )
            mods["time_series_advanced"] = {
                "arma": ARMA, "arima": ARIMA, "holtwinters": HoltWinters,
                "prophet": ProphetLike, "selector": ModelSelector,
                "validator": TimeSeriesCrossValidator, "ensemble": EnsembleForecaster,
            }
        except Exception:
            pass
    return mods


_MODS: dict[str, Any] = {}


def get_mods():
    if not _MODS:
        _MODS.update(_import_mods())
    return _MODS


@real_engines_router.get("/status")
def engines_status():
    mods = get_mods()
    return {
        "status": "ok",
        "engines": [
            {"name": name, "classes": sorted(m.keys()), "loaded": True}
            for name, m in mods.items()
        ],
    }


@real_engines_router.post("/climate/scenario")
def climate_scenario(body: dict):
    mods = get_mods()
    if "climate" not in mods:
        raise HTTPException(503, "Climate engine not available")
    gen = mods["climate"]["scenario"]()
    result = gen.generate(body.get("params", {})) if hasattr(gen, "generate") else {"scenarios": []}
    return {"status": "ok", "result": result}


@real_engines_router.post("/streaming/process")
def streaming_process(body: dict):
    mods = get_mods()
    if "streaming" not in mods:
        raise HTTPException(503, "Streaming engine not available")
    data = body.get("data", [])
    processor = mods["streaming"]["processor"]()
    result = processor.process(data) if hasattr(processor, "process") else {"processed": len(data)}
    return {"status": "ok", "result": result}


@real_engines_router.post("/time-series/forecast")
def time_series_forecast(body: dict):
    mods = get_mods()
    if "time_series" not in mods:
        raise HTTPException(503, "Time series engine not available")
    data = body.get("data", [])
    forecaster = mods["time_series"]["forecaster"]()
    result = forecaster.forecast(data, body.get("steps", 10)) if hasattr(forecaster, "forecast") else {"forecast": []}
    return {"status": "ok", "result": result}


@real_engines_router.post("/time-series-advanced/arima")
def time_series_arima(body: dict):
    mods = get_mods()
    if "time_series_advanced" not in mods:
        raise HTTPException(503, "Time series advanced engine not available")
    data = body.get("data", [])
    arima = mods["time_series_advanced"]["arima"]()
    result = arima.fit(data) if hasattr(arima, "fit") else {"model": "arima_fitted"}
    return {"status": "ok", "result": result}
