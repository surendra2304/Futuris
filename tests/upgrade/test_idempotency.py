import unittest
from futuris.upgrade.idempotency import IdempotencyConflict, IdempotencyStore


class IdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay(self):
        s = IdempotencyStore()
        payload = {"target": "x"}
        self.assertIsNone(await s.begin("k1", payload))
        await s.commit("k1", payload, 200, {"ok": True})
        prior = await s.begin("k1", payload)
        self.assertEqual(prior.response, {"ok": True})

    async def test_conflict(self):
        s = IdempotencyStore()
        await s.commit("k1", {"target": "x"}, 200, {})
        with self.assertRaises(IdempotencyConflict):
            await s.begin("k1", {"target": "y"})


if __name__ == "__main__":
    unittest.main()
