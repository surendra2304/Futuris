import asyncio
import unittest

from futuris.upgrade.scheduler import DistributedLeaseTable, SafeScheduler, ScheduleSpec


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_flight(self):
        leases = DistributedLeaseTable()
        self.assertTrue(await leases.acquire("j1", "a"))
        self.assertFalse(await leases.acquire("j1", "b"))
        self.assertTrue(await leases.release("j1", "a"))

    async def test_lease_rejects_invalid_identifiers_and_expiry(self):
        leases = DistributedLeaseTable()
        for args in [("", "a", 60), ("j1", "", 60), ("j1", "a", 0), ("j1", "a", -1)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                await leases.acquire(args[0], args[1], args[2])

    async def test_expiration_includes_full_fractional_second(self):
        leases = DistributedLeaseTable()
        self.assertTrue(await leases.acquire("j1", "a", lease_seconds=60))
        lease = leases._leases["j1"]
        self.assertGreaterEqual((lease.expires_at - lease.acquired_at).total_seconds(), 60.0)

    async def test_invalid_schedule_specs_are_rejected(self):
        for args in [("", 60, "UTC"), ("j1", 0, "UTC"), ("j1", -1, "UTC"), ("j1", 1, "")]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                ScheduleSpec(args[0], args[1], args[2])
        with self.assertRaises(ValueError):
            ScheduleSpec("j1", True)

    async def test_disabled_schedule_does_not_execute(self):
        scheduler = SafeScheduler()
        calls = 0

        async def work() -> int:
            nonlocal calls
            calls += 1
            return 7

        result = await scheduler.run_once(ScheduleSpec("j1", 60, enabled=False), "worker-a", work)
        self.assertIsNone(result)
        self.assertEqual(calls, 0)

    async def test_run_once(self):
        scheduler = SafeScheduler()
        result = await scheduler.run_once(
            ScheduleSpec("j1", 60), "worker-a", lambda: asyncio.sleep(0, result=7)
        )
        self.assertEqual(result, 7)
        self.assertEqual(scheduler._tasks, set())

    async def test_shutdown_cancels_and_drains_active_job(self):
        scheduler = SafeScheduler()
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def work() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        run = asyncio.create_task(scheduler.run_once(ScheduleSpec("j1", 60), "worker-a", work))
        await asyncio.wait_for(started.wait(), timeout=1)
        await scheduler.shutdown()
        with self.assertRaises(asyncio.CancelledError):
            await run
        self.assertTrue(cleaned.is_set())
        self.assertFalse(scheduler._tasks)
        self.assertTrue(await scheduler.leases.acquire("j1", "worker-b"))
        await scheduler.leases.release("j1", "worker-b")


if __name__ == "__main__":
    unittest.main()
