"""Core domain schemas, models, and validation rules for FUTURIS."""

from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from futuris.core.enums import (
    ConfidenceLevel,
    ForecastEventType,
    ForecastStatus,
    ResolutionMethod,
    ScenarioType,
    SignalClass,
    SourceTrust,
)


class EvidenceRef(BaseModel):
    """Reference to frozen point-in-time supporting evidence and provenance."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for the evidence snapshot.",
    )
    source: str = Field(
        ...,
        description="Source system or telemetry origin name.",
    )
    source_trust: SourceTrust = Field(
        ...,
        description="Assessed trust level of the origin source.",
    )
    signal_class: SignalClass = Field(
        ...,
        description="Classification of signal characteristics.",
    )
    as_of: datetime = Field(
        ...,
        description="Point-in-time UTC timestamp when the snapshot was captured.",
    )
    snapshot_path: str = Field(
        ...,
        description="File or object storage path pointing to frozen snapshot.",
    )
    content_hash: str = Field(
        ...,
        description="SHA-256 hash verifying integrity of the snapshot data.",
    )


class Driver(BaseModel):
    """Explanatory driver influencing the forecast trajectory."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        description="Name of the driver or feature.",
    )
    direction: Literal["positive", "negative", "neutral"] = Field(
        ...,
        description="Direction of impact on the target variable.",
    )
    strength: float = Field(
        ...,
        description="Relative magnitude or weight of the driver influence.",
    )
    leading_or_lagging: Literal["leading", "lagging", "coincident"] = Field(
        ...,
        description="Temporal relationship of driver to target variable.",
    )
    evidence_refs: list[UUID] = Field(
        default_factory=list,
        description="List of evidence_ids directly substantiating this driver.",
    )


class Forecast(BaseModel):
    """Central Forecast domain entity.

    Confidence vs Probability Note:
    confidence is a META-confidence metric indicating calibration quality,
    data sparsity, regime novelty, and recent model degradation. It is strictly
    independent of probability. A forecast can have 70% probability with low
    confidence (e.g. sparse historical observations in a novel regime).
    """

    model_config = ConfigDict(extra="forbid")

    forecast_id: UUID = Field(
        default_factory=uuid4,
        description="Unique sortable identifier for the forecast entity.",
    )
    target: str = Field(
        ...,
        description="Target metric (e.g. 'service:checkout:capacity_exceedance_24h').",
    )
    as_of: datetime = Field(
        ...,
        description="Point-in-time UTC timestamp when the forecast was generated.",
    )
    horizon: timedelta = Field(
        ...,
        description="Prediction time span duration from as_of.",
    )
    expires_at: datetime = Field(
        ...,
        description="UTC expiration timestamp of the forecast horizon.",
    )
    prediction: float = Field(
        ...,
        description="Central point estimate of the forecast.",
    )
    range_lower: float = Field(
        ...,
        description="Lower bound of uncertainty interval.",
    )
    range_upper: float = Field(
        ...,
        description="Upper bound of uncertainty interval.",
    )
    probability: float | None = Field(
        default=None,
        description="Calibrated probability in [0, 1] for binary/event outcomes.",
    )
    confidence: ConfidenceLevel = Field(
        ...,
        description="Meta-confidence level on calibration quality (independent of probability).",
    )
    drivers: list[Driver] = Field(
        default_factory=list,
        description="Identified drivers and explanatory factors.",
    )
    evidence: list[EvidenceRef] = Field(
        default_factory=list,
        description="Frozen point-in-time evidence snapshots anchoring the forecast.",
    )
    model_version: str = Field(
        ...,
        description="Exact model adapter name and configuration hash.",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Conditions that must remain approximately true for validity.",
    )
    review_at: datetime = Field(
        ...,
        description="UTC timestamp for scheduled reassessment or review.",
    )
    status: ForecastStatus = Field(
        default=ForecastStatus.ACTIVE,
        description="Lifecycle status of the forecast.",
    )
    scenario_id: UUID | None = Field(
        default=None,
        description="Associated scenario identifier if part of a counterfactual branch.",
    )

    @field_validator("probability")
    @classmethod
    def validate_probability(cls, v: float | None) -> float | None:
        """Validate probability is bounded in [0, 1] when provided."""
        if v is not None and not (0.0 <= v <= 1.0):
            msg = f"Probability must be between 0.0 and 1.0, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("horizon")
    @classmethod
    def validate_horizon(cls, v: timedelta) -> timedelta:
        """Validate forecast horizon is strictly positive."""
        if v <= timedelta(0):
            msg = f"Forecast horizon must be strictly positive (> 0), got {v}"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def validate_forecast_invariants(self) -> "Forecast":
        """Cross-field validation for temporal integrity, ranges, and evidence references."""
        # Validate interval ordering: range_lower <= prediction <= range_upper
        if not (self.range_lower <= self.prediction <= self.range_upper):
            msg = (
                f"Range bounds violation: range_lower ({self.range_lower}) <= "
                f"prediction ({self.prediction}) <= range_upper ({self.range_upper}) required"
            )
            raise ValueError(msg)

        # Validate temporal ordering: as_of < expires_at
        if self.as_of >= self.expires_at:
            msg = (
                f"Temporal violation: as_of ({self.as_of}) must precede "
                f"expires_at ({self.expires_at})"
            )
            raise ValueError(msg)

        # Validate review_at: review_at >= as_of
        if self.review_at < self.as_of:
            msg = (
                f"Review schedule violation: review_at ({self.review_at}) "
                f"must be >= as_of ({self.as_of})"
            )
            raise ValueError(msg)

        # Cross-field validation: drivers' evidence_refs must reference evidence on the forecast
        valid_evidence_ids = {e.evidence_id for e in self.evidence}
        for driver in self.drivers:
            for ref_id in driver.evidence_refs:
                if ref_id not in valid_evidence_ids:
                    msg = (
                        f"Driver '{driver.name}' references evidence_id '{ref_id}' "
                        f"which is not present in forecast evidence list"
                    )
                    raise ValueError(msg)

        return self


class Outcome(BaseModel):
    """Ground truth resolution and recorded outcome for a forecast."""

    model_config = ConfigDict(extra="forbid")

    outcome_id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for the resolved outcome record.",
    )
    forecast_id: UUID = Field(
        ...,
        description="Identifier of the target forecast being resolved.",
    )
    observed_value: float | None = Field(
        default=None,
        description="Observed numeric value for continuous metrics.",
    )
    event_occurred: bool | None = Field(
        default=None,
        description="Observed binary outcome for event-type forecasts.",
    )
    resolved_at: datetime = Field(
        ...,
        description="Point-in-time UTC timestamp when the outcome was observed/resolved.",
    )
    resolution_method: ResolutionMethod = Field(
        ...,
        description="Resolution mechanism utilized.",
    )
    ambiguity_note: str | None = Field(
        default=None,
        description="Explanation note if resolution encountered ambiguities.",
    )
    resolution_rule_version: str = Field(
        ...,
        description="Version hash or identifier of the resolution rule logic.",
    )


class Scenario(BaseModel):
    """Scenario branch and counterfactual assumptions definition."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for the scenario simulation.",
    )
    name: str = Field(
        ...,
        description="Human-readable title or label for the scenario.",
    )
    scenario_type: ScenarioType = Field(
        ...,
        description="Scenario categorization type.",
    )
    assumptions_override: dict[str, Any] = Field(
        default_factory=dict,
        description="Key-value assumption adjustments applied to this scenario branch.",
    )
    created_by: str = Field(
        ...,
        description="User or automated agent identifier that generated this scenario.",
    )
    parent_forecast_id: UUID | None = Field(
        default=None,
        description="Parent baseline forecast ID if branched from an existing projection.",
    )


class ForecastEvent(BaseModel):
    """Lifecycle event emitted during forecast creation, update, or resolution."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for the domain event.",
    )
    forecast_id: UUID | None = Field(
        default=None,
        description="Associated forecast identifier if event targets a specific forecast.",
    )
    event_type: ForecastEventType = Field(
        ...,
        description="Type of domain event.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Event payload payload data and metadata.",
    )
    emitted_at: datetime = Field(
        ...,
        description="Point-in-time UTC timestamp when the event was emitted.",
    )


class ModelInfo(BaseModel):
    """Metadata, versioning, and calibration scores for a registered forecasting model."""

    model_config = ConfigDict(extra="forbid")

    model_version: str = Field(
        ...,
        description="Unique identifier and version for the model.",
    )
    family: str = Field(
        ...,
        description="Model family (e.g. 'statsforecast.AutoARIMA', 'ensemble').",
    )
    config_hash: str = Field(
        ...,
        description="SHA-256 configuration hash of hyper-parameters and feature pipeline.",
    )
    promoted_at: datetime = Field(
        ...,
        description="Point-in-time UTC timestamp when the model was promoted to active serving.",
    )
    benchmark_scores: dict[str, float] = Field(
        default_factory=dict,
        description="Validation benchmark scores (e.g. CRPS, MAPE, Brier score, coverage rate).",
    )
