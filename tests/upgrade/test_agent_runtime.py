import asyncio
import unittest

from futuris.upgrade.agent_runtime import AgentRuntime
from futuris.upgrade.cancellation import CancellationToken
from futuris.upgrade.models import JobState


class AgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_first_non_none_result(self):
        runtime = AgentRuntime(max_iterations=4)
        calls: list[int] = []

        async def work(iteration: int) -> object | None:
            calls.append(iteration)
            return None if iteration < 3 else {"complete": True}

        result = await runtime.run(work)
        self.assertEqual(result.state, JobState.SUCCEEDED)
        self.assertEqual(result.value, {"complete": True})
        self.assertEqual(result.attempts, 3)
        self.assertEqual(calls, [1, 2, 3])
        self.assertIsNone(result.error)

    async def test_iteration_limit_returns_failed_result(self):
        runtime = AgentRuntime(max_iterations=2)
        result = await runtime.run(lambda _iteration: asyncio.sleep(0, result=None))
        self.assertEqual(result.state, JobState.FAILED)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.error, "iteration limit reached")

    async def test_work_exception_returns_failed_result(self):
        runtime = AgentRuntime(max_iterations=3)

        async def fail(_iteration: int) -> object:
            raise ValueError("provider rejected request")

        result = await runtime.run(fail)
        self.assertEqual(result.state, JobState.FAILED)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.error, "ValueError: provider rejected request")

    async def test_max_iterations_must_be_positive_integer(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AgentRuntime(max_iterations=value)

    async def test_cancel_token_interrupts_running_work_and_drains_cleanup(self):
        runtime = AgentRuntime()
        token = CancellationToken()
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def work(_iteration: int) -> object:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        task = asyncio.create_task(runtime.run(work, token=token))
        await asyncio.wait_for(started.wait(), timeout=1)
        token.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        self.assertTrue(cleaned.is_set())

    async def test_pre_cancelled_token_never_invokes_work(self):
        token = CancellationToken()
        token.cancel()
        calls = 0

        async def work(_iteration: int) -> object:
            nonlocal calls
            calls += 1
            return "unexpected"

        with self.assertRaises(asyncio.CancelledError):
            await AgentRuntime().run(work, token=token)
        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main()
