import unittest
from uuid import uuid4
from futuris.upgrade.models import ForecastEnvelope
from futuris.upgrade.quality import ForecastQualityGate, coverage_score


class QualityTests(unittest.TestCase):
    def test_valid_forecast(self):
        f = ForecastEnvelope(
            forecast_id=uuid4(), target="x", prediction=10, lower=8, upper=12,
            confidence=0.8, model_version="naive@1", evidence_ids=["e1"]
        )
        self.assertTrue(ForecastQualityGate().evaluate(f).passed)

    def test_bad_interval(self):
        f = ForecastEnvelope(target="x", prediction=10, lower=12, upper=13, confidence=.8, model_version="m", evidence_ids=["e"])
        self.assertFalse(ForecastQualityGate().evaluate(f).passed)

    def test_coverage(self):
        self.assertEqual(coverage_score(["a", "b"], ["b"]), 0.5)


if __name__ == "__main__":
    unittest.main()
