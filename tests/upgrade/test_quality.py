import unittest
from uuid import uuid4

from futuris.upgrade.models import ForecastEnvelope
from futuris.upgrade.quality import ForecastQualityGate, coverage_score


class QualityTests(unittest.TestCase):
    def test_valid_forecast(self):
        f = ForecastEnvelope(
            forecast_id=uuid4(),
            target="x",
            prediction=10,
            lower=8,
            upper=12,
            confidence=0.8,
            model_version="naive@1",
            evidence_ids=["e1"],
        )
        self.assertTrue(ForecastQualityGate().evaluate(f).passed)

    def test_bad_interval(self):
        f = ForecastEnvelope(
            target="x",
            prediction=10,
            lower=12,
            upper=13,
            confidence=0.8,
            model_version="m",
            evidence_ids=["e"],
        )
        self.assertFalse(ForecastQualityGate().evaluate(f).passed)

    def test_valid_core_forecast_is_supported(self):
        from datetime import UTC, datetime, timedelta

        from futuris.core.enums import ConfidenceLevel, EvidenceClass, SignalClass, SourceTrust
        from futuris.core.hashing import content_hash_of
        from futuris.core.schemas import EvidenceRef, Forecast

        now = datetime.now(UTC)
        evidence = EvidenceRef(
            source="quality-test",
            source_trust=SourceTrust.HIGH,
            signal_class=SignalClass.TELEMETRY,
            as_of=now,
            snapshot_path="inline://quality/test.json",
            content_hash=content_hash_of({"observed": 1}),
            evidence_class=EvidenceClass.SYNTHETIC,
        )
        forecast = Forecast(
            target="service:checkout:capacity_exceedance_24h",
            as_of=now,
            horizon=timedelta(hours=1),
            expires_at=now + timedelta(hours=1),
            prediction=10,
            range_lower=8,
            range_upper=12,
            probability=0.2,
            confidence=ConfidenceLevel.HIGH,
            evidence=[evidence],
            model_version="naive@1",
            review_at=now + timedelta(minutes=30),
        )

        valid, reasons = ForecastQualityGate.validate_forecast(forecast)

        self.assertTrue(valid, reasons)

    def test_unsupported_object_is_rejected_without_fabricating_fields(self):
        from types import SimpleNamespace

        sparse = SimpleNamespace(target="x", prediction=0.0, confidence="high")
        valid, reasons = ForecastQualityGate.validate_forecast(sparse)

        self.assertFalse(valid)
        self.assertEqual(
            reasons,
            ["unsupported forecast type; expected ForecastEnvelope or core Forecast"],
        )
        with self.assertRaisesRegex(ValueError, "unsupported forecast type"):
            ForecastQualityGate.require(sparse)

    def test_coverage(self):
        self.assertEqual(coverage_score(["a", "b"], ["b"]), 0.5)


if __name__ == "__main__":
    unittest.main()
