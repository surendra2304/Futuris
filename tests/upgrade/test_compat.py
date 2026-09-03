import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from futuris.upgrade.compat import ForecastCompatibilityAdapter, FuturisRuntimeContext
from futuris.upgrade.forecast_guard import ProductionDataSource, SourcePolicy
from futuris.upgrade.models import Principal
from futuris.upgrade.policy import Policy, PolicyEngine


class FakeEngine:
    async def orchestrate(self, **kwargs):
        return [SimpleNamespace(
            forecast_id=uuid4(), target=kwargs["target"], as_of=kwargs["as_of"],
            prediction=10, range_lower=8, range_upper=12, probability=.4,
            confidence="high", model_version="m1",
            evidence=[SimpleNamespace(evidence_id=uuid4())],
            assumptions=["stable"]
        )]


class CompatTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter(self):
        ctx = FuturisRuntimeContext(
            Principal("p","t"),
            PolicyEngine(Policy(max_horizon_hours=100)),
            SourcePolicy(production=False),
        )
        adapter = ForecastCompatibilityAdapter(FakeEngine(), ctx)
        results = await adapter.forecast(
            target="x", as_of=datetime.now(UTC), horizon=timedelta(hours=1),
            source=ProductionDataSource("telemetry", lambda a,b: None)
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].target, "x")


if __name__ == "__main__":
    unittest.main()
