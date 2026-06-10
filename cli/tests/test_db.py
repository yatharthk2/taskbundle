"""Ledger: incrementing ids, JSON round-trip, missing-id handling."""
import tempfile
import unittest
from pathlib import Path

from taskbundle import db


class TestLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "db.sqlite")

    def tearDown(self):
        self._tmp.cleanup()

    def test_ids_increment(self):
        a = db.log_command("init", "ok", db_path=self.db)
        b = db.log_command("init", "ok", db_path=self.db)
        self.assertEqual((a, b), (1, 2))

    def test_details_round_trip(self):
        cid = db.log_command(
            "run", "ok", task_id="t1", summary="done",
            details={"passed": ["a"], "failed": []}, db_path=self.db,
        )
        rec = db.get_command(cid, self.db)
        self.assertEqual(rec["command"], "run")
        self.assertEqual(rec["task_id"], "t1")
        self.assertEqual(rec["details"], {"passed": ["a"], "failed": []})

    def test_missing_is_none(self):
        self.assertIsNone(db.get_command(999, self.db))


if __name__ == "__main__":
    unittest.main()
