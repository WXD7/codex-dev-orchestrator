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

    def test_completed_dependency_is_ready_at_creation(self):
        first = self.db.create_task(self.project["id"], "First")
        self.db.update_task(first["id"], status="done")

        second = self.db.create_task(
            self.project["id"], "Second", dependencies=[first["id"]]
        )

        self.assertEqual(second["status"], "ready")
        self.assertTrue(self.db.dependencies_complete(second["id"]))

    def test_dependency_cycle_is_rejected(self):
        first = self.db.create_task(self.project["id"], "First")
        second = self.db.create_task(
            self.project["id"], "Second", dependencies=[first["id"]]
        )
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.db.add_dependency(first["id"], second["id"])

    def test_required_artifacts_round_trip_as_a_list(self):
        task = self.db.create_task(
            self.project["id"],
            "Deliver files",
            role="implementer",
            required_artifacts=["docs/spec.md", "tests/test_spec.py"],
        )
        self.assertEqual(
            task["required_artifacts"],
            ["docs/spec.md", "tests/test_spec.py"],
        )
        listed = next(
            item for item in self.db.list_tasks(self.project["id"])
            if item["id"] == task["id"]
        )
        self.assertEqual(listed["required_artifacts"], task["required_artifacts"])

    def test_required_artifacts_reject_unsafe_paths(self):
        unsafe = (
            "",
            "/tmp/spec.md",
            "../spec.md",
            "docs/../spec.md",
            "docs//spec.md",
            "docs\\spec.md",
            "docs/",
        )
        for path in unsafe:
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "required_artifacts"):
                    self.db.create_task(
                        self.project["id"],
                        "Unsafe artifact",
                        role="implementer",
                        required_artifacts=[path],
                    )

    def test_read_only_role_cannot_require_file_artifacts(self):
        for role in ("coordinator", "planner", "reviewer"):
            with self.subTest(role=role):
                with self.assertRaisesRegex(ValueError, "implementer or qa"):
                    self.db.create_task(
                        self.project["id"],
                        "Invalid file owner",
                        role=role,
                        required_artifacts=["docs/spec.md"],
                    )

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

    def test_pending_approvals_and_reviews_span_projects(self):
        other = self.db.create_project("Other", "/tmp/other")
        first = self.db.create_task(self.project["id"], "Plan A", role="coordinator")
        second = self.db.create_task(other["id"], "Plan B", role="coordinator")
        self.db.create_approval(first["id"], "Approve A?", kind="complete")
        self.db.create_approval(second["id"], "Approve B?", kind="resume")

        pending = self.db.list_pending_approvals()
        self.assertEqual(
            [(item["task_id"], item["project_name"]) for item in pending],
            [(first["id"], "Demo"), (second["id"], "Other")],
        )
        self.assertEqual(pending[0]["question"], "Approve A?")

        self.db.resolve_approval(first["id"], True, "ok")
        self.assertEqual(
            [item["task_id"] for item in self.db.list_pending_approvals()], [second["id"]]
        )

    def test_tasks_awaiting_review_are_listed_with_project_context(self):
        task = self.db.create_task(self.project["id"], "Implement")
        self.assertEqual(self.db.list_tasks_awaiting_review(), [])
        self.db.update_task(task["id"], status="review", summary="Added endpoint")
        awaiting = self.db.list_tasks_awaiting_review()
        self.assertEqual(len(awaiting), 1)
        self.assertEqual(awaiting[0]["task_id"], task["id"])
        self.assertEqual(awaiting[0]["project_name"], "Demo")
        self.assertEqual(awaiting[0]["summary"], "Added endpoint")

    def test_alert_events_filter_noise_and_group_repeated_signals(self):
        task = self.db.create_task(self.project["id"], "Observe runtime")
        self.db.add_event(
            task["id"],
            None,
            "codex.stderr",
            {"line": "2026-08-21T08:00:00Z WARN codex_skills::interface: ignoring interface.icon_small: bad path"},
        )
        for timestamp in ("08:01:00", "08:02:00"):
            self.db.add_event(
                task["id"],
                None,
                "codex.stderr",
                {"line": "2026-08-21T%sZ WARN sandbox violation" % timestamp},
            )
        self.db.add_event(
            task["id"], None, "task.quota_deferred", {"reason": "五小时额度待刷新"}
        )
        self.db.add_event(task["id"], None, "run.failed", {"error": "tests failed"})

        alerts = self.db.list_alert_events(task["id"])

        self.assertEqual(
            [item["type"] for item in alerts],
            ["run.failed", "task.quota_deferred", "codex.stderr"],
        )
        self.assertEqual(alerts[2]["occurrences"], 2)
        self.assertEqual(alerts[2]["severity"], "error")
        self.assertEqual(alerts[2]["message"], "WARN sandbox violation")


if __name__ == "__main__":
    unittest.main()
