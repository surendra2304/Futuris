import unittest
from datetime import UTC, datetime, timedelta
from futuris.upgrade.forecast_guard import DataLeakageError, SourcePolicy, ProductionDataSource, validate_point_in_time, compute_horizon_steps


class ForecastGuardTests(unittest.TestCase):
    def test_future_data_rejected(self):
        as_of = datetime(2026, 1, 1, tzinfo=UTC)
        with self.assertRaises(DataLeakageError):
            validate_point_in_time([as_of + timedelta(minutes=1)], as_of)

    def test_steps(self):
        self.assertEqual(compute_horizon_steps(timedelta(hours=1), 15), 4)

    def test_synthetic_rejected_in_prod(self):
        with self.assertRaises(ValueError):
            SourcePolicy(True).validate(ProductionDataSource("synthetic", lambda a,b: None, synthetic=True))


if __name__ == "__main__":
    unittest.main()
