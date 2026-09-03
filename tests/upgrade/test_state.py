import unittest
from futuris.upgrade.models import JobState
from futuris.upgrade.state import InvalidTransition, StateMachine


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


if __name__ == "__main__":
    unittest.main()
