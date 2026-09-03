import unittest
from futuris.upgrade.retry import retry_async


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_transient(self):
        state = {"n": 0}
        async def op():
            state["n"] += 1
            if state["n"] < 3:
                raise RuntimeError("temporary")
            return 9
        result = await retry_async(op, attempts=3, base_delay=0.001)
        self.assertEqual(result, 9)
        self.assertEqual(state["n"], 3)

    async def test_non_retryable(self):
        state = {"n": 0}
        async def op():
            state["n"] += 1
            raise ValueError("bad")
        with self.assertRaises(ValueError):
            await retry_async(op, attempts=5, base_delay=0.001, is_retryable=lambda exc: False)
        self.assertEqual(state["n"], 1)


if __name__ == "__main__":
    unittest.main()
