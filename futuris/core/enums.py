"""Core domain enumerations for the FUTURIS platform."""

from enum import StrEnum


class ForecastStatus(StrEnum):
    """Lifecycle status of a forecast."""

    DRAFT = "draft"
    ACTIVE = "active"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"
    INSUFFICIENT_DATA = "insufficient_data"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ScenarioType(StrEnum):
    """Type categorization for scenario trees and simulations."""

    BASELINE = "baseline"
    UPSIDE = "upside"
    DOWNSIDE = "downside"
    STRESS = "stress"
    COUNTERFACTUAL = "counterfactual"
    USER_DEFINED = "user_defined"


class SignalClass(StrEnum):
    """Classification of signal origins feeding the forecasting engine."""

    TELEMETRY = "telemetry"
    HISTORICAL = "historical"
    EXTERNAL = "external"
    BEHAVIORAL = "behavioral"
    AGENT_OBSERVATION = "agent_observation"
    HUMAN_INPUT = "human_input"


class HorizonBucket(StrEnum):
    """Temporal granularity bucket for forecast horizons."""

    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"


class ForecastEventType(StrEnum):
    """Domain events emitted across the forecast lifecycle."""

    FORECAST_CREATED = "forecast_created"
    FORECAST_UPDATED = "forecast_updated"
    FORECAST_THRESHOLD_CROSSED = "forecast_threshold_crossed"
    FORECAST_INVALIDATED = "forecast_invalidated"
    FORECAST_OUTCOME_RECORDED = "forecast_outcome_recorded"
    FORECAST_CANCELLED = "forecast_cancelled"
    DATA_STALENESS_DETECTED = "data_staleness_detected"
    INSUFFICIENT_DATA_DETECTED = "insufficient_data_detected"
    MODEL_PROMOTED = "model_promoted"
    MODEL_DEGRADED = "model_degraded"


class ResolutionMethod(StrEnum):
    """Method utilized to verify and record ground-truth forecast outcomes."""

    AUTOMATIC = "automatic"
    AUTOMATED_TELEMETRY = "automatic"
    HUMAN = "human"
    AMBIGUOUS = "ambiguous"


class SourceTrust(StrEnum):
    """Assessment of external/internal data source trustworthiness."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNTRUSTED = "untrusted"


class ConfidenceLevel(StrEnum):
    """Meta-confidence assessment regarding model calibration and data quality.

    CRITICAL DISTINCTION: This represents meta-confidence about calibration quality,
    data sparsity, regime novelty, and recent model degradation. It is STRICTLY INDEPENDENT
    of the probability of the outcome. For example, a forecast may estimate a 70% probability
    with low confidence due to data sparsity in a new market regime.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT_DATA = "insufficient_data"
