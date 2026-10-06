"""SQLAlchemy 2.0 ORM database models mapping to domain schemas with JSONB support."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Interval,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""

    pass


class ForecastModel(Base):
    """SQLAlchemy model for central Forecast entities."""

    __tablename__ = "forecasts"

    forecast_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    target: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    horizon: Mapped[datetime] = mapped_column(Interval, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prediction: Mapped[float] = mapped_column(Float, nullable=False)
    range_lower: Mapped[float] = mapped_column(Float, nullable=False)
    range_upper: Mapped[float] = mapped_column(Float, nullable=False)
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    model_version: Mapped[str] = mapped_column(String(255), nullable=False)
    assumptions: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list, nullable=False
    )
    review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    scenario_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scenarios.scenario_id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    drivers: Mapped[list[dict]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list, nullable=False
    )
    predictive_distribution: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )
    intervals: Mapped[list[dict]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list, nullable=False
    )
    calibration_metrics: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )
    model_metadata: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Provenance labels.  They were domain-only fields for a long time, so a
    # forecast built from live Stratex telemetry read back as ``synthetic``
    # after a round trip: the served label contradicted the stored evidence.
    # Nullable on purpose -- rows written before this column existed have no
    # recorded class and must degrade to the weakest label, never to ``live``.
    evidence_class: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    evidence_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_refs: Mapped[list["EvidenceRefModel"]] = relationship(
        "EvidenceRefModel",
        back_populates="forecast",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # Declared so the unit of work orders ``INSERT INTO scenarios`` before
    # ``INSERT INTO forecasts``: SQLAlchemy only derives cross-table flush
    # ordering from relationships, and without this a scenario and its
    # forecast created in the same flush would violate the ``scenario_id``
    # foreign key (which is enforced for real, see ``futuris/storage/db.py``).
    scenario: Mapped["ScenarioModel | None"] = relationship(
        "ScenarioModel", foreign_keys="ForecastModel.scenario_id", lazy="raise"
    )

    __table_args__ = (
        Index("ix_forecasts_target_as_of", "target", "as_of"),
        Index("ix_forecasts_status", "status"),
    )


class EvidenceRefModel(Base):
    """SQLAlchemy model for EvidenceRef frozen snapshot attachments."""

    __tablename__ = "evidence_refs"

    evidence_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    forecast_id: Mapped[UUID] = mapped_column(
        ForeignKey("forecasts.forecast_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    source_trust: Mapped[str] = mapped_column(String(32), nullable=False)
    signal_class: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_class: Mapped[str | None] = mapped_column(String(32), nullable=True)

    forecast: Mapped["ForecastModel"] = relationship(
        "ForecastModel", back_populates="evidence_refs"
    )


class OutcomeModel(Base):
    """SQLAlchemy model for resolved ground-truth outcomes."""

    __tablename__ = "outcomes"

    outcome_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    forecast_id: Mapped[UUID] = mapped_column(
        ForeignKey("forecasts.forecast_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_occurred: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolution_method: Mapped[str] = mapped_column(String(64), nullable=False)
    ambiguity_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)


class ScenarioModel(Base):
    """SQLAlchemy model for counterfactual scenarios."""

    __tablename__ = "scenarios"

    scenario_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(64), nullable=False)
    assumptions_override: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_forecast_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("forecasts.forecast_id", ondelete="SET NULL", use_alter=True), nullable=True
    )


class ForecastEventModel(Base):
    """SQLAlchemy model for emitted domain events."""

    __tablename__ = "forecast_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    forecast_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("forecasts.forecast_id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )
    emitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # The audit trail points at ``forecasts`` through a real, enforced foreign
    # key.  SQLAlchemy derives cross-table flush ordering from relationships
    # only, and unrelated mappers are ordered by ``(module, class name)`` -
    # which puts ``ForecastEventModel`` *before* ``ForecastModel``.  Without
    # this relationship the ``forecast_created`` event is flushed before its
    # own forecast, so the insert dies with ``FOREIGN KEY constraint failed``
    # and the forecast row is never written at all (the flush aborts).  The
    # relationship exists purely to pin that ordering; it is never cascaded
    # from or loaded lazily (``lazy="raise"`` keeps the audit trail explicit).
    forecast: Mapped["ForecastModel | None"] = relationship("ForecastModel", lazy="raise")


class IntelXNoticeModel(Base):
    """Durable advisory copy of an IntelX event received through Memora."""

    __tablename__ = "intelx_notices"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_agent: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )


class ObservationModel(Base):
    """Ingested operational telemetry observations."""

    __tablename__ = "observations"

    observation_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    series_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    tags: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False
    )

    __table_args__ = (Index("ix_observations_series_time", "series_id", "observed_at"),)


class SignalSourceModel(Base):
    """Registered telemetry and external signal sources."""

    __tablename__ = "signal_sources"

    source_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    signal_class: Mapped[str] = mapped_column(String(64), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
    # See the note on ``ForecastEventModel.forecast``: this relationship pins
    # the flush order so ``model_registry`` is inserted before the runs that
    # reference it when both are pending in the same flush.
    model_registry: Mapped["ModelRegistryModel | None"] = relationship(
        "ModelRegistryModel", lazy="raise"
    )


class ApiKeyModel(Base):
    """Hashed API keys with role-based access control."""

    __tablename__ = "api_keys"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLogModel(Base):
    """Append-only audit trail for all mutating system actions."""

    __tablename__ = "audit_logs"

    audit_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_label: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
