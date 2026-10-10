"""Make forecasts.idempotency_key unique.

The FRIDAY delegation path is idempotent on ``idempotency_key``: it checks for
an existing forecast with the key before creating one. That check-then-create
is a race -- a concurrent retry storm with the same key passed the check N
times and inserted N duplicate forecasts (measured: 8 parallel delegations
with one ``friday_request_id`` produced 8 forecasts). The unique index makes
the database itself the arbiter: the first insert wins and every loser gets an
IntegrityError, which the API maps to an idempotent replay of the winner.

Rows written before this migration may already contain duplicates from that
race. The migration keeps the FIRST forecast per key (the one idempotent
replays have been returning) and removes later duplicates together with their
dependent evidence, events and outcomes, so no orphaned rows are left behind
on either dialect (SQLite only cascades when FK enforcement is on, so the
child rows are deleted explicitly).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07 15:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DUPLICATES = """
SELECT forecast_id FROM (
    SELECT forecast_id,
           ROW_NUMBER() OVER (
               PARTITION BY idempotency_key
               ORDER BY created_at ASC, forecast_id ASC
           ) AS rn
    FROM forecasts
    WHERE idempotency_key IS NOT NULL
) ranked
WHERE rn > 1
"""


def _delete_duplicates() -> None:
    # A temp table keeps the duplicate-id subquery readable and works on both
    # SQLite and PostgreSQL within the migration's connection.
    op.execute(
        """
        CREATE TEMP TABLE _duplicate_forecasts AS
        """
        + _DUPLICATES
    )
    for table in ("evidence_refs", "forecast_events", "outcomes", "forecasts"):
        op.execute(
            f"DELETE FROM {table} WHERE forecast_id IN "
            "(SELECT forecast_id FROM _duplicate_forecasts)"
        )
    op.execute("DROP TABLE _duplicate_forecasts")


def upgrade() -> None:
    _delete_duplicates()
    # The non-unique index created by 0002 must go before the unique one with
    # the same name can be created.
    op.drop_index("ix_forecasts_idempotency_key", table_name="forecasts")
    op.create_index(
        "ix_forecasts_idempotency_key",
        "forecasts",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_forecasts_idempotency_key", table_name="forecasts")
    op.create_index("ix_forecasts_idempotency_key", "forecasts", ["idempotency_key"], unique=False)
