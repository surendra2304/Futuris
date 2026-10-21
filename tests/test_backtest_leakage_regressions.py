"""Regression tests for timestamp normalization in backtest leakage checks."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from futuris.evaluation.backtest import DataLeakageError, validate_backtest_leakage


def test_backtest_leakage_normalizes_naive_dataframe_index_to_utc():
    as_of = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    historical = pd.DataFrame(
        {"value": [1.0, 2.0]},
        index=pd.DatetimeIndex([datetime(2026, 9, 12, 10), datetime(2026, 9, 12, 11)]),
    )
    assert validate_backtest_leakage(historical, as_of=as_of) is True

    future = pd.DataFrame(
        {"value": [1.0, 2.0]},
        index=pd.DatetimeIndex([datetime(2026, 9, 12, 11), datetime(2026, 9, 12, 13)]),
    )
    with pytest.raises(DataLeakageError, match="1 points exceed as_of"):
        validate_backtest_leakage(future, as_of=as_of)


def test_backtest_leakage_rejects_invalid_timestamp_column():
    data = pd.DataFrame({"timestamp": ["2026-09-12T11:00:00Z", "not-a-timestamp"]})
    as_of = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(DataLeakageError, match="invalid timestamp"):
        validate_backtest_leakage(data, as_of=as_of)


def test_backtest_leakage_wraps_malformed_list_timestamp_as_domain_error():
    as_of = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(DataLeakageError, match="invalid timestamp"):
        validate_backtest_leakage(["not-a-timestamp"], as_of=as_of)
