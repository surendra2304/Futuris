"""Migration health tests: the Alembic chain must build the ORM schema exactly.

Bug C2 was invisible because nothing ever ran a migration: ``alembic upgrade
head`` raised on SQLite and the initial migration was missing five columns that
the ORM declares. These tests run the real chain against a throwaway SQLite
file and compare the result with ``Base.metadata``.
"""

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from futuris.infra.config import settings
from futuris.storage.models import Base

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _run_upgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run ``alembic upgrade head`` against a temp SQLite file; return its path."""
    db_path = tmp_path / "migration_test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setattr(settings, "DATABASE_URL", db_url)

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    return str(db_path)


def _orm_schema() -> dict[str, set[str]]:
    return {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.sorted_tables
    }


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_alembic_upgrade_head_builds_the_complete_orm_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Every ORM table and column must exist after a clean migration run."""
    db_path = _run_upgrade(tmp_path, monkeypatch)

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        migrated_tables = set(inspector.get_table_names()) - {"alembic_version"}
        orm_schema = _orm_schema()

        assert orm_schema.keys() <= migrated_tables, (
            f"migrations did not create: {sorted(orm_schema.keys() - migrated_tables)}"
        )

        for table, expected_columns in orm_schema.items():
            actual_columns = {c["name"] for c in inspector.get_columns(table)}
            missing = expected_columns - actual_columns
            assert not missing, f"table '{table}' is missing columns: {sorted(missing)}"
    finally:
        engine.dispose()


def test_forecast_read_after_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The migrated database must serve a forecast round-trip (the C2 failure mode)."""
    db_path = _run_upgrade(tmp_path, monkeypatch)

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT predictive_distribution, intervals, calibration_metrics, "
                    "model_metadata, idempotency_key FROM forecasts"
                )
            ).fetchall()
            assert rows == []
    finally:
        engine.dispose()


def test_alembic_downgrade_returns_to_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The chain must be reversible; downgrade base leaves no application tables."""
    db_path = _run_upgrade(tmp_path, monkeypatch)
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setattr(settings, "DATABASE_URL", db_url)

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    command.downgrade(cfg, "base")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert remaining == set(), f"downgrade left tables behind: {sorted(remaining)}"
    finally:
        engine.dispose()
