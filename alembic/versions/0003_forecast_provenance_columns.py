"""Persist forecast and evidence provenance labels.

``Forecast.evidence_class`` / ``Forecast.evidence_source`` and
``EvidenceRef.evidence_class`` were domain-only fields: a forecast built from
live Stratex telemetry was written without its label and read back as
``synthetic``, so every GET contradicted the evidence the row was created from.
The same drift applies to the frozen evidence snapshots.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06 18:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the provenance columns. Legacy rows stay NULL and read as synthetic."""
    with op.batch_alter_table("forecasts") as batch_op:
        batch_op.add_column(sa.Column("evidence_class", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("evidence_source", sa.String(length=255), nullable=True))
    op.create_index("ix_forecasts_evidence_class", "forecasts", ["evidence_class"])
    with op.batch_alter_table("evidence_refs") as batch_op:
        batch_op.add_column(sa.Column("evidence_class", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_index("ix_forecasts_evidence_class", table_name="forecasts")
    with op.batch_alter_table("evidence_refs") as batch_op:
        batch_op.drop_column("evidence_class")
    with op.batch_alter_table("forecasts") as batch_op:
        batch_op.drop_column("evidence_source")
        batch_op.drop_column("evidence_class")
