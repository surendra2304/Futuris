import asyncio
import unittest
from decimal import Decimal
from futuris.upgrade.budget import BudgetExceeded, BudgetManager


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_reservation(self):
        mgr = BudgetManager()
        await mgr.configure("t1", "r1", Decimal("10"))
        await mgr.reserve("t1", "r1", Decimal("7"))
        with self.assertRaises(BudgetExceeded):
            await mgr.reserve("t1", "r1", Decimal("4"))
        await mgr.settle("t1", "r1", Decimal("7"), Decimal("6"))
        snap = await mgr.snapshot("t1", "r1")
        self.assertEqual(snap.spent, Decimal("6"))

    async def test_concurrent_reservations_do_not_overspend(self):
        mgr = BudgetManager()
        await mgr.configure("t1", "r1", Decimal("10"))
        async def reserve():
            try:
                await mgr.reserve("t1", "r1", Decimal("2"))
                return True
            except BudgetExceeded:
                return False
        results = await asyncio.gather(*(reserve() for _ in range(10)))
        self.assertEqual(sum(results), 5)


if __name__ == "__main__":
    unittest.main()
