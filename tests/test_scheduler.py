from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from orchestrator.database import Database
from orchestrator.git_service import GitService
from orchestrator.models import AgentRunResult, PreflightResult
from orchestrator.scheduler import TaskScheduler
from tests.helpers import make_git_repo


class FakeAgent:
    def preflight(self):
        return PreflightResult(True, "fake", "Logged in using ChatGPT", [])

    def run(self, run_id, role, worktree, prompt, session_id, on_event):
        on_event("thread.started", {"thread_id": "session-%s" % run_id})
        final = {
            "outcome": "completed",
            "summary": "Plan created",
            "handoff_notes": "Implement child after approval",
            "tests": [],
            "proposed_tasks": [
                {
                    "title": "Implement child",
                    "description": "Implement the accepted plan",
                    "role": "implementer",
                    "depends_on_titles": [],
                    "requires_approval": False,
                    "auto_start": False,
                }
            ],
            "messages": [],
            "recommended_stage": "done",
            "approval_question": "",
        }
        return AgentRunResult(
            0, "complete", final, "session-%s" % run_id, {}, "", ["fake"]
        )


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        repo = make_git_repo(root)
        self.db = Database(root / "db.sqlite3")
        self.db.initialize()
        self.project = self.db.create_project("Demo", str(repo), "main")
        self.git = GitService(root / "worktrees")
        self.scheduler = TaskScheduler(self.db, self.git, FakeAgent(), max_workers=1)
        self.scheduler.start()

    def tearDown(self):
        self.scheduler.stop()
        self.temp.cleanup()

    def wait_for(self, task_id, status, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            task = self.db.get_task(task_id)
            if task["status"] == status:
                return task
            time.sleep(0.05)
        self.fail("Task did not reach %s; current=%s" % (status, self.db.get_task(task_id)["status"]))

    def test_coordinator_delegates_but_children_wait_for_human_approval(self):
        parent = self.db.create_task(
            self.project["id"],
            "Plan feature",
            role="coordinator",
            allow_delegation=True,
            requires_approval=True,
        )
        self.scheduler.submit(parent["id"])
        parent = self.wait_for(parent["id"], "waiting_approval")
        self.assertEqual(len(parent["children"]), 1)
        child = self.db.get_task(parent["children"][0]["id"])
        self.assertEqual(child["status"], "blocked")

        self.scheduler.resolve_approval(parent["id"], True, "Approved")
        self.assertEqual(self.db.get_task(parent["id"])["status"], "done")
        self.assertEqual(self.db.get_task(child["id"])["status"], "ready")

    def test_monitor_does_not_repeat_an_agent_reported_block(self):
        task = self.db.create_task(self.project["id"], "Needs external service")
        self.db.update_task(
            task["id"], status="blocked", error="External service unavailable"
        )

        time.sleep(1.8)

        self.assertEqual(self.db.get_task(task["id"])["status"], "blocked")
        self.assertEqual(self.db.list_runs(task["id"]), [])

    def test_empty_agent_block_summary_still_gets_a_stable_reason(self):
        task = self.db.create_task(self.project["id"], "Ambiguous blocker")
        final = {
            "outcome": "blocked",
            "summary": "",
            "handoff_notes": "",
            "proposed_tasks": [],
            "messages": [],
            "recommended_stage": "blocked",
        }

        self.scheduler._apply_result(task, final, None)

        blocked = self.db.get_task(task["id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertTrue(blocked["error"])


if __name__ == "__main__":
    unittest.main()
