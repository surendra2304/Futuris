import unittest
from futuris.upgrade.decision import DecisionEngine
from futuris.upgrade.models import ForecastEnvelope, ActionRisk


class DecisionTests(unittest.TestCase):
    def test_governed_is_advisory_only(self):
        f = ForecastEnvelope(
            target="x", prediction=120, lower=100, upper=140,
            probability=.8, confidence=.9, model_version="m1", evidence_ids=["e1"]
        )
        decisions = DecisionEngine().recommend(f)
        self.assertEqual(decisions[0].risk, ActionRisk.GOVERNED)
        self.assertTrue(decisions[0].requires_authorization)


if __name__ == "__main__":
    unittest.main()
