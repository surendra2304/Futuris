"""Close the ORM/migration schema drift.

The ORM grew five ``forecasts`` columns and the whole ``intelx_notices`` table
without matching migrations, so any database created by Alembic failed as soon
as a forecast was read back ("no such column: predictive_distribution") and
IntelX event dedupe had nowhere to write.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json():
    """JSONB on Postgres, native JSON on SQLite (matches the ORM models)."""
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _upgrade_forecasts() -> None:
    with op.batch_alter_table("forecasts") as batch_op:
        batch_op.add_column(
            sa.Column("predictive_distribution", _json(), nullable=False, server_default="{}")
        )
        batch_op.add_column(sa.Column("intervals", _json(), nullable=False, server_default="[]"))
        batch_op.add_column(
            sa.Column("calibration_metrics", _json(), nullable=False, server_default="{}")
        )
        batch_op.add_column(
            sa.Column("model_metadata", _json(), nullable=False, server_default="{}")
        )
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=255), nullable=True))
        batch_op.create_index("ix_forecasts_idempotency_key", ["idempotency_key"])


def _upgrade_observations() -> None:
    """Rename the legacy observation columns to the ORM names and add `unit`."""
    with op.batch_alter_table("observations") as batch_op:
        batch_op.alter_column(
            "timestamp",
            new_column_name="observed_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch_op.alter_column(
            "signal_class",
            new_column_name="series_id",
            existing_type=sa.String(length=50),
            type_=sa.String(length=255),
            nullable=False,
        )
        batch_op.alter_column(
            "payload",
            new_column_name="tags",
            existing_type=_json(),
            nullable=False,
        )
        batch_op.add_column(
            sa.Column("unit", sa.String(length=64), nullable=False, server_default="")
        )

    # Index changes run in a second batch pass: the first pass rebuilds the
    # table, so the renamed columns are only visible to reflection afterwards.
    with op.batch_alter_table("observations") as batch_op:
        batch_op.drop_index("ix_observations_source_timestamp")
        batch_op.create_index("ix_observations_series_id", ["series_id"])
        batch_op.create_index("ix_observations_observed_at", ["observed_at"])


def _upgrade_signal_sources() -> None:
    """Align signal_sources with the ORM (name/trust_level/first_seen_at)."""
    with op.batch_alter_table("signal_sources") as batch_op:
        batch_op.alter_column(
            "source_name",
            new_column_name="name",
            existing_type=sa.String(length=255),
            nullable=False,
        )
        batch_op.alter_column(
            "source_trust",
            new_column_name="trust_level",
            existing_type=sa.String(length=50),
            type_=sa.String(length=32),
            nullable=False,
        )
        batch_op.add_column(sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        "UPDATE signal_sources SET first_seen_at = COALESCE(last_seen_at, CURRENT_TIMESTAMP) "
        "WHERE first_seen_at IS NULL"
    )

    with op.batch_alter_table("signal_sources") as batch_op:
        batch_op.alter_column(
            "first_seen_at", existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch_op.drop_column("config")


def _upgrade_intelx_notices() -> None:
    op.create_table(
        "intelx_notices",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("source_agent", sa.String(length=64), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_intelx_notices_received_at", "intelx_notices", ["received_at"])


def upgrade() -> None:
    _upgrade_forecasts()
    _upgrade_observations()
    _upgrade_signal_sources()
    _upgrade_intelx_notices()


def downgrade() -> None:
    op.drop_index("ix_intelx_notices_received_at", table_name="intelx_notices")
    op.drop_table("intelx_notices")

    with op.batch_alter_table("signal_sources") as batch_op:
        batch_op.add_column(sa.Column("config", _json(), nullable=False, server_default="{}"))
        batch_op.alter_column(
            "trust_level",
            new_column_name="source_trust",
            existing_type=sa.String(length=32),
            type_=sa.String(length=50),
            nullable=False,
        )
        batch_op.alter_column(
            "name",
            new_column_name="source_name",
            existing_type=sa.String(length=255),
            nullable=False,
        )
        batch_op.drop_column("first_seen_at")

    with op.batch_alter_table("observations") as batch_op:
        batch_op.drop_column("unit")
        batch_op.alter_column(
            "tags", new_column_name="payload", existing_type=_json(), nullable=False
        )
        batch_op.alter_column(
            "series_id",
            new_column_name="signal_class",
            existing_type=sa.String(length=255),
            type_=sa.String(length=50),
            nullable=False,
        )
        batch_op.alter_column(
            "observed_at",
            new_column_name="timestamp",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )

    with op.batch_alter_table("observations") as batch_op:
        batch_op.drop_index("ix_observations_series_id")
        batch_op.drop_index("ix_observations_observed_at")
        batch_op.create_index("ix_observations_source_timestamp", ["source", "timestamp"])
    with op.batch_alter_table("forecasts") as batch_op:
        batch_op.drop_index("ix_forecasts_idempotency_key")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("model_metadata")
        batch_op.drop_column("calibration_metrics")
        batch_op.drop_column("intervals")
        batch_op.drop_column("predictive_distribution")
