from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.sqlite3")
        self.db.initialize()
        self.project = self.db.create_project("Demo", "/tmp/demo")

    def tearDown(self):
        self.temp.cleanup()

    def test_dependency_blocks_then_unblocks(self):
        first = self.db.create_task(self.project["id"], "First")
        second = self.db.create_task(
            self.project["id"], "Second", dependencies=[first["id"]]
        )
        self.assertEqual(second["status"], "blocked")
        self.assertFalse(self.db.dependencies_complete(second["id"]))

        self.db.update_task(first["id"], status="done")
        changed = self.db.refresh_unblocked_tasks(self.project["id"])
        self.assertEqual(changed, [second["id"]])
        self.assertEqual(self.db.get_task(second["id"])["status"], "ready")

    def test_dependency_cycle_is_rejected(self):
        first = self.db.create_task(self.project["id"], "First")
        second = self.db.create_task(
            self.project["id"], "Second", dependencies=[first["id"]]
        )
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.db.add_dependency(first["id"], second["id"])

    def test_agent_reported_block_is_not_treated_as_dependency_block(self):
        task = self.db.create_task(self.project["id"], "Needs external access")
        self.db.update_task(task["id"], status="blocked", error="Network unavailable")

        changed = self.db.refresh_unblocked_tasks(self.project["id"])

        self.assertEqual(changed, [])
        self.assertEqual(self.db.get_task(task["id"])["status"], "blocked")

    def test_messages_and_approval_round_trip(self):
        task = self.db.create_task(self.project["id"], "Plan")
        message = self.db.add_message(task["id"], "Human", "Please continue")
        self.assertEqual(self.db.list_messages(task["id"])[0]["id"], message["id"])
        approval = self.db.create_approval(task["id"], "Approve plan?", kind="complete")
        self.assertEqual(self.db.pending_approval(task["id"])["kind"], "complete")
        resolved = self.db.resolve_approval(task["id"], True, "Looks good")
        self.assertEqual(resolved["status"], "approved")
        self.assertIsNone(self.db.pending_approval(task["id"]))


if __name__ == "__main__":
    unittest.main()
