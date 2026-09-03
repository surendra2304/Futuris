from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class BudgetLedger:
    limit: Decimal
    reserved: Decimal = Decimal("0")
    spent: Decimal = Decimal("0")

    @property
    def available(self) -> Decimal:
        return self.limit - self.reserved - self.spent


class BudgetManager:
    """Concurrency-safe in-process budget ledger; replace store with transactional DB/Redis in prod."""

    def __init__(self) -> None:
        self._ledgers: dict[tuple[str, str], BudgetLedger] = {}
        self._lock = asyncio.Lock()

    async def configure(self, tenant_id: str, run_id: str, limit: Decimal) -> None:
        if limit < 0:
            raise ValueError("negative budget")
        async with self._lock:
            self._ledgers[(tenant_id, run_id)] = BudgetLedger(limit=limit)

    async def reserve(self, tenant_id: str, run_id: str, amount: Decimal) -> bool:
        if amount < 0:
            raise ValueError("negative reservation")
        async with self._lock:
            ledger = self._ledgers[(tenant_id, run_id)]
            if ledger.available < amount:
                raise BudgetExceeded("budget reservation denied")
            ledger.reserved += amount
            return True

    async def settle(self, tenant_id: str, run_id: str, reserved: Decimal, actual: Decimal) -> None:
        if min(reserved, actual) < 0:
            raise ValueError("negative settlement")
        async with self._lock:
            ledger = self._ledgers[(tenant_id, run_id)]
            if reserved > ledger.reserved:
                raise ValueError("reservation exceeds outstanding reservation")
            ledger.reserved -= reserved
            ledger.spent += actual
            if ledger.spent > ledger.limit:
                raise BudgetExceeded("actual spend exceeded configured budget")

    async def snapshot(self, tenant_id: str, run_id: str) -> BudgetLedger:
        async with self._lock:
            source = self._ledgers[(tenant_id, run_id)]
            return BudgetLedger(source.limit, source.reserved, source.spent)
