import tempfile
import unittest
from pathlib import Path
from futuris.upgrade.persistence import DurableStateStore


class PersistenceTests(unittest.TestCase):
    def test_job_and_outbox(self):
        with tempfile.TemporaryDirectory() as d:
            store = DurableStateStore(Path(d) / "f.db")
            self.assertTrue(store.upsert_job("j1", "t1", "p1", "running", 1, {"x": 1}, "now"))
            self.assertEqual(store.get_job("j1")["state"], "running")
            self.assertTrue(store.append_outbox("e1", "t1", "forecast.completed", {"id": "j1"}))
            self.assertEqual(len(store.pending_outbox()), 1)
            self.assertTrue(store.mark_outbox_delivered("e1"))
            self.assertEqual(store.pending_outbox(), [])


if __name__ == "__main__":
    unittest.main()
