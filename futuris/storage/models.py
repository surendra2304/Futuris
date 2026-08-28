"""SQLAlchemy 2.0 ORM models for FUTURIS persistence layer."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Interval,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """Base declarative class for all storage entities."""

    type_annotation_map = {
        dict[str, Any]: JSON().with_variant(JSONB, "postgresql"),
        list[dict[str, Any]]: JSON().with_variant(JSONB, "postgresql"),
        list[str]: JSON().with_variant(JSONB, "postgresql"),
    }


class ForecastModel(Base):
    """Persisted Forecast aggregate root."""

    __tablename__ = "forecasts"

    forecast_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon: Mapped[Any] = mapped_column(Interval, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prediction: Mapped[float] = mapped_column(Float, nullable=False)
    range_lower: Mapped[float] = mapped_column(Float, nullable=False)
    range_upper: Mapped[float] = mapped_column(Float, nullable=False)
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str] = mapped_column(String(50), nullable=False)
    drivers: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list, nullable=False
    )
    model_version: Mapped[str] = mapped_column(String(255), nullable=False)
    assumptions: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list, nullable=False
    )
    review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    scenario_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scenarios.scenario_id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    evidence: Mapped[list["EvidenceRefModel"]] = relationship(
        "EvidenceRefModel",
        back_populates="forecast",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    outcomes: Mapped[list["OutcomeModel"]] = relationship(
        "OutcomeModel",
        back_populates="forecast",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_forecasts_target_as_of", "target", "as_of"),
        Index("ix_forecasts_status", "status"),
    )


class EvidenceRefModel(Base):
    """Frozen point-in-time supporting evidence snapshots."""

    __tablename__ = "evidence_refs"

    evidence_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    forecast_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("forecasts.forecast_id", ondelete="CASCADE"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    source_trust: Mapped[str] = mapped_column(String(50), nullable=False)
    signal_class: Mapped[str] = mapped_column(String(50), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    forecast: Mapped["ForecastModel | None"] = relationship(
        "ForecastModel", back_populates="evidence"
    )


class OutcomeModel(Base):
    """Ground truth resolution outcomes."""

    __tablename__ = "outcomes"

    outcome_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    forecast_id: Mapped[UUID] = mapped_column(
        ForeignKey("forecasts.forecast_id", ondelete="CASCADE"), nullable=False, index=True
    )
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_occurred: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolution_method: Mapped[str] = mapped_column(String(50), nullable=False)
    ambiguity_note: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    resolution_rule_version: Mapped[str] = mapped_column(String(255), nullable=False)

    forecast: Mapped["ForecastModel"] = relationship("ForecastModel", back_populates="outcomes")


class ScenarioModel(Base):
    """Scenario simulation definitions and counterfactual overrides."""

    __tablename__ = "scenarios"

    scenario_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(50), nullable=False)
    assumptions_override: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_forecast_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("forecasts.forecast_id", ondelete="SET NULL"), nullable=True
    )


class ForecastEventModel(Base):
    """Append-only audit trail and point-in-time history of forecast events."""

    __tablename__ = "forecast_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    forecast_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("forecasts.forecast_id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )
    emitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (
        Index("ix_forecast_events_forecast_id_emitted", "forecast_id", "emitted_at"),
    )


class ObservationModel(Base):
    """Raw or transformed signal observations ingested from connectors."""

    __tablename__ = "observations"

    observation_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    signal_class: Mapped[str] = mapped_column(String(50), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )

    __table_args__ = (
        Index("ix_observations_source_timestamp", "source", "timestamp"),
    )


class SignalSourceModel(Base):
    """Signal connector registrations and trust configurations."""

    __tablename__ = "signal_sources"

    source_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    signal_class: Mapped[str] = mapped_column(String(50), nullable=False)
    source_trust: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelRegistryModel(Base):
    """Registered forecasting models and promotion state."""

    __tablename__ = "model_registry"

    model_version: Mapped[str] = mapped_column(String(255), primary_key=True)
    family: Mapped[str] = mapped_column(String(255), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    benchmark_scores: Mapped[dict[str, float]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )


class EvaluationRunModel(Base):
    """Evaluation benchmark run logs and backtesting scores."""

    __tablename__ = "evaluation_runs"

    run_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    model_version: Mapped[str] = mapped_column(
        ForeignKey("model_registry.model_version", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_name: Mapped[str] = mapped_column(String(255), nullable=False)
    metrics: Mapped[dict[str, float]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
