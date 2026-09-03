import unittest
from futuris.upgrade.scenario import Scenario, ScenarioEngine, ScenarioValidationError


class ScenarioTests(unittest.TestCase):
    def test_deterministic(self):
        engine = ScenarioEngine()
        s = Scenario("stress", {"demand": 10}, seed=42)
        a = engine.run(100, s, draws=4)
        b = engine.run(100, s, draws=4)
        self.assertEqual(a, b)

    def test_nan_rejected(self):
        engine = ScenarioEngine()
        s = Scenario("bad", {"x": float("nan")})
        with self.assertRaises(ScenarioValidationError):
            engine.run(100, s)


if __name__ == "__main__":
    unittest.main()
