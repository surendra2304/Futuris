"""Initial database schema for FUTURIS storage entities.

Revision ID: 0001
Revises:
Create Date: 2026-08-28 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. scenarios table
    op.create_table(
        "scenarios",
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("scenario_type", sa.String(length=50), nullable=False),
        sa.Column("assumptions_override", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("parent_forecast_id", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("scenario_id"),
    )

    # 2. forecasts table
    op.create_table(
        "forecasts",
        sa.Column("forecast_id", sa.UUID(), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon", sa.Interval(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prediction", sa.Float(), nullable=False),
        sa.Column("range_lower", sa.Float(), nullable=False),
        sa.Column("range_upper", sa.Float(), nullable=False),
        sa.Column("probability", sa.Float(), nullable=True),
        sa.Column("confidence", sa.String(length=50), nullable=False),
        sa.Column("drivers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_version", sa.String(length=255), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.scenario_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("forecast_id"),
    )
    op.create_index("ix_forecasts_target_as_of", "forecasts", ["target", "as_of"])
    op.create_index("ix_forecasts_status", "forecasts", ["status"])

    # Foreign key for scenario -> parent_forecast_id
    op.create_foreign_key(
        "fk_scenarios_parent_forecast_id",
        "scenarios",
        "forecasts",
        ["parent_forecast_id"],
        ["forecast_id"],
        ondelete="SET NULL",
    )

    # 3. evidence_refs table
    op.create_table(
        "evidence_refs",
        sa.Column("evidence_id", sa.UUID(), nullable=False),
        sa.Column("forecast_id", sa.UUID(), nullable=True),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("source_trust", sa.String(length=50), nullable=False),
        sa.Column("signal_class", sa.String(length=50), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_path", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["forecast_id"], ["forecasts.forecast_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("evidence_id"),
    )
    op.create_index("ix_evidence_refs_forecast_id", "evidence_refs", ["forecast_id"])

    # 4. outcomes table
    op.create_table(
        "outcomes",
        sa.Column("outcome_id", sa.UUID(), nullable=False),
        sa.Column("forecast_id", sa.UUID(), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=True),
        sa.Column("event_occurred", sa.Boolean(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_method", sa.String(length=50), nullable=False),
        sa.Column("ambiguity_note", sa.String(length=2048), nullable=True),
        sa.Column("resolution_rule_version", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["forecast_id"], ["forecasts.forecast_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("outcome_id"),
    )
    op.create_index("ix_outcomes_forecast_id", "outcomes", ["forecast_id"])

    # 5. forecast_events table
    op.create_table(
        "forecast_events",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("forecast_id", sa.UUID(), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("emitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["forecast_id"], ["forecasts.forecast_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_forecast_events_forecast_id_emitted",
        "forecast_events",
        ["forecast_id", "emitted_at"],
    )

    # 6. observations table
    op.create_table(
        "observations",
        sa.Column("observation_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("signal_class", sa.String(length=50), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("observation_id"),
    )
    op.create_index("ix_observations_source_timestamp", "observations", ["source", "timestamp"])

    # 7. signal_sources table
    op.create_table(
        "signal_sources",
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("signal_class", sa.String(length=50), nullable=False),
        sa.Column("source_trust", sa.String(length=50), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("source_id"),
    )

    # 8. model_registry table
    op.create_table(
        "model_registry",
        sa.Column("model_version", sa.String(length=255), nullable=False),
        sa.Column("family", sa.String(length=255), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("benchmark_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("model_version"),
    )
    op.create_index("ix_model_registry_is_active", "model_registry", ["is_active"])

    # 9. evaluation_runs table
    op.create_table(
        "evaluation_runs",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("model_version", sa.String(length=255), nullable=False),
        sa.Column("dataset_name", sa.String(length=255), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_version"], ["model_registry.model_version"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )


def downgrade() -> None:
    op.drop_table("evaluation_runs")
    op.drop_table("model_registry")
    op.drop_table("signal_sources")
    op.drop_table("observations")
    op.drop_table("forecast_events")
    op.drop_table("outcomes")
    op.drop_table("evidence_refs")
    op.drop_table("forecasts")
    op.drop_table("scenarios")
