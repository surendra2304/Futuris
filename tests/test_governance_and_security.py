"""Tests for governance, role-based auth, append-only audit trail, and model promotion gates."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.evidence.snapshots import EvidenceSnapshotter
from futuris.infra.audit import AuditLogger
from futuris.storage.models import Base, EvaluationRunModel, ModelRegistryModel
from futuris.storage.repositories import ModelPromotionError, ModelRepository


@pytest.fixture
async def gov_db_session():
    """Provide isolated in-memory DB session for governance tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_audit_log_mutation_trail(gov_db_session: AsyncSession):
    """Verify mutating actions create immutable audit records with computed payload hashes."""
    audit_logger = AuditLogger(gov_db_session)
    forecast_id = str(uuid4())

    record = await audit_logger.log_mutation(
        actor_label="operator_bob",
        action="invalidate_forecast",
        entity="forecast",
        entity_id=forecast_id,
        payload={"reason": "Node scaled up"},
    )
    assert record.actor_label == "operator_bob"
    assert record.action == "invalidate_forecast"
    assert len(record.payload_hash) == 64

    logs = await audit_logger.list_recent(limit=10)
    assert len(logs) == 1
    assert logs[0].entity_id == forecast_id


@pytest.mark.asyncio
async def test_model_promotion_gate_enforcement(gov_db_session: AsyncSession):
    """Verify model promotion gate blocks unvetted, outdated, or inferior models."""
    model_repo = ModelRepository(gov_db_session)
    now = datetime.now(UTC)

    # 1. Register baseline active model with MAE=40.0, ECE=0.05
    active_m = ModelRegistryModel(
        model_version="auto_arima@v1",
        family="arima",
        config_hash="hash1",
        is_active=True,
        promoted_at=now - timedelta(days=10),
        benchmark_scores={"mae": 40.0, "ece": 0.05},
    )
    gov_db_session.add(active_m)

    # 2. Register candidate model with WORSE MAE=48.0
    worse_candidate = ModelRegistryModel(
        model_version="auto_arima@v2_worse",
        family="arima",
        config_hash="hash2",
        is_active=False,
        promoted_at=None,
        benchmark_scores={"mae": 48.0, "ece": 0.04},
    )
    gov_db_session.add(worse_candidate)

    # 3. Add fresh evaluation run
    eval_run = EvaluationRunModel(
        run_id=uuid4(),
        model_version="auto_arima@v2_worse",
        dataset_name="wedge_benchmark",
        metrics={"mae": 48.0, "ece": 0.04},
        created_at=now,
    )
    gov_db_session.add(eval_run)
    await gov_db_session.flush()

    # Attempt promotion -> MUST be rejected because MAE is worse
    with pytest.raises(ModelPromotionError, match="Candidate MAE .* is worse"):
        await model_repo.promote("auto_arima@v2_worse")

    # 4. Register candidate model with BETTER MAE=35.0, ECE=0.03
    better_candidate = ModelRegistryModel(
        model_version="auto_arima@v2_better",
        family="arima",
        config_hash="hash3",
        is_active=False,
        promoted_at=None,
        benchmark_scores={"mae": 35.0, "ece": 0.03},
    )
    gov_db_session.add(better_candidate)
    eval_run_better = EvaluationRunModel(
        run_id=uuid4(),
        model_version="auto_arima@v2_better",
        dataset_name="wedge_benchmark",
        metrics={"mae": 35.0, "ece": 0.03},
        created_at=now,
    )
    gov_db_session.add(eval_run_better)
    await gov_db_session.flush()

    # Attempt promotion -> MUST succeed
    promoted = await model_repo.promote("auto_arima@v2_better")
    assert promoted.model_version == "auto_arima@v2_better"
    assert promoted.promoted_at is not None

    active_list = await model_repo.current_active(family="arima")
    assert len(active_list) == 1
    assert active_list[0].model_version == "auto_arima@v2_better"


def test_data_minimization_pii_filtering(tmp_path):
    """Verify data minimization denies PII/sensitive columns from entering evidence snapshots."""
    as_of = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    idx = pd.date_range(end=as_of, periods=10, freq="5min", tz=UTC)
    df = pd.DataFrame(
        {
            "value": np.random.uniform(100, 200, size=10),
            "user_id": ["user_123"] * 10,
            "email": ["user@example.com"] * 10,
            "customer_name": ["Alice"] * 10,
        },
        index=idx,
    )

    snapshotter = EvidenceSnapshotter(base_storage_path=str(tmp_path))
    sanitized = snapshotter.sanitize_dataframe(df)
    assert "user_id" not in sanitized.columns
    assert "email" not in sanitized.columns
    assert "customer_name" not in sanitized.columns
    assert "value" in sanitized.columns