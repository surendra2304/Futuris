import sqlite3
import unittest
from futuris.upgrade.checkpoint import CheckpointManager


class CheckpointTests(unittest.TestCase):
    def test_checksum(self):
        conn = sqlite3.connect(":memory:")
        mgr = CheckpointManager(conn)
        cp = mgr.save("j1", 1, "running", {"x": 1})
        latest = mgr.load_latest("j1")
        self.assertEqual(cp, latest)

    def test_checksum_detects_tamper(self):
        conn = sqlite3.connect(":memory:")
        mgr = CheckpointManager(conn)
        mgr.save("j1", 1, "running", {"x": 1})
        conn.execute("UPDATE checkpoints SET payload='{\"x\":99}' WHERE job_id='j1'")
        conn.commit()
        with self.assertRaises(ValueError):
            mgr.load_latest("j1")


if __name__ == "__main__":
    unittest.main()
