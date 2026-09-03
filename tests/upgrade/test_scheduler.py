import unittest
from futuris.upgrade.scheduler import DistributedLeaseTable, SafeScheduler, ScheduleSpec


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_flight(self):
        leases = DistributedLeaseTable()
        self.assertTrue(await leases.acquire("j1", "a"))
        self.assertFalse(await leases.acquire("j1", "b"))
        self.assertTrue(await leases.release("j1", "a"))

    async def test_run_once(self):
        scheduler = SafeScheduler()
        result = await scheduler.run_once(
            ScheduleSpec("j1", 60), "worker-a", lambda: __import__("asyncio").sleep(0, result=7)
        )
        self.assertEqual(result, 7)


if __name__ == "__main__":
    unittest.main()
