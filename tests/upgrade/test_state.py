import asyncio
import unittest
from datetime import UTC, datetime

from futuris.upgrade.models import JobState
from futuris.upgrade.state import InvalidTransition, StateMachine, VersionedState


class StateTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_transition(self):
        sm = StateMachine()
        snap = await sm.transition(0, JobState.QUEUED)
        self.assertEqual(snap.version, 1)
        self.assertEqual(snap.state, JobState.QUEUED)

    async def test_stale_version_rejected(self):
        sm = StateMachine()
        await sm.transition(0, JobState.QUEUED)
        with self.assertRaises(InvalidTransition):
            await sm.transition(0, JobState.RUNNING)

    async def test_terminal_state_rejected(self):
        sm = StateMachine(JobState.SUCCEEDED)
        with self.assertRaises(InvalidTransition):
            await sm.transition(0, JobState.RUNNING)

    async def test_default_timestamps_are_created_per_instance(self):
        before = datetime.now(UTC)
        first = VersionedState(JobState.CREATED)
        await asyncio.sleep(0.002)
        second = VersionedState(JobState.CREATED)
        self.assertGreaterEqual(first.updated_at, before)
        self.assertGreater(second.updated_at, first.updated_at)

    async def test_transition_updates_timestamp_and_increments_version(self):
        machine = StateMachine()
        original = await machine.snapshot()
        await asyncio.sleep(0.002)
        updated = await machine.transition(original.version, JobState.QUEUED)
        self.assertGreater(updated.updated_at, original.updated_at)
        self.assertEqual(updated.version, original.version + 1)


if __name__ == "__main__":
    unittest.main()
