import asyncio
import unittest

from futuris.upgrade.cancellation import CancellationToken, await_or_cancel, gather_cancel_safe


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_wait_and_status(self):
        token = CancellationToken()
        self.assertFalse(token.is_cancelled())
        waiter = asyncio.create_task(token.wait())
        await asyncio.sleep(0)
        token.cancel()
        await asyncio.wait_for(waiter, timeout=1)
        self.assertTrue(token.is_cancelled())

    async def test_throw_if_cancelled(self):
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(asyncio.CancelledError):
            token.throw_if_cancelled()

    async def test_await_or_cancel_returns_completed_value(self):
        token = CancellationToken()
        self.assertEqual(await await_or_cancel(asyncio.sleep(0, result=11), token), 11)
        self.assertFalse(token.is_cancelled())

    async def test_await_or_cancel_cancels_operation(self):
        token = CancellationToken()
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def work() -> int:
            started.set()
            try:
                await asyncio.Event().wait()
                return 1
            finally:
                cleaned.set()

        task = asyncio.create_task(await_or_cancel(work(), token))
        await asyncio.wait_for(started.wait(), timeout=1)
        token.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        self.assertTrue(cleaned.is_set())

    async def test_gather_returns_results(self):
        results = await gather_cancel_safe(
            asyncio.sleep(0, result="a"),
            asyncio.sleep(0, result="b"),
        )
        self.assertEqual(results, ["a", "b"])

    async def test_failure_cancels_and_drains_siblings(self):
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def blocked() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        async def fail() -> None:
            await started.wait()
            raise ValueError("work failed")

        with self.assertRaisesRegex(ValueError, "work failed"):
            await gather_cancel_safe(blocked(), fail())
        self.assertTrue(cleaned.is_set())

    async def test_caller_cancellation_cancels_every_child(self):
        started_count = 0
        cleaned_count = 0
        both_started = asyncio.Event()
        both_cleaned = asyncio.Event()

        async def blocked() -> None:
            nonlocal started_count, cleaned_count
            started_count += 1
            if started_count == 2:
                both_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned_count += 1
                if cleaned_count == 2:
                    both_cleaned.set()

        task = asyncio.create_task(gather_cancel_safe(blocked(), blocked()))
        await asyncio.wait_for(both_started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        self.assertTrue(both_cleaned.is_set())


if __name__ == "__main__":
    unittest.main()
