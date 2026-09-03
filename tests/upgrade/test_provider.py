import asyncio
import unittest
from futuris.upgrade.models import FailureKind
from futuris.upgrade.provider import ProviderAdapter, ProviderRouter


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_success(self):
        async def call(**kwargs):
            return {"ok": True}
        result = await ProviderAdapter("p1", call).invoke(request_id="r1")
        self.assertTrue(result.ok)
        self.assertEqual(result.output["ok"], True)

    async def test_timeout_is_typed(self):
        async def call(**kwargs):
            raise TimeoutError("late")
        result = await ProviderAdapter("p1", call).invoke()
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, FailureKind.TIMEOUT)
        self.assertTrue(result.retryable)

    async def test_router_falls_back_to_second_provider(self):
        async def bad(**kwargs):
            raise TimeoutError
        async def good(**kwargs):
            return "ok"
        router = ProviderRouter([ProviderAdapter("bad", bad), ProviderAdapter("good", good)])
        result = await router.invoke()
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "ok")


if __name__ == "__main__":
    unittest.main()
