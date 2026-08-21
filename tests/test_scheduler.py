from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from orchestrator.agents import AgentRegistry
from orchestrator.database import Database
from orchestrator.git_service import GitError, GitService
from orchestrator.models import AgentRunResult, PreflightResult
from orchestrator.quota import QuotaSnapshot, QuotaWindow
from orchestrator.scheduler import QuotaDeferred, TaskScheduler
from tests.helpers import make_git_repo


class FakeAgent:
    name = "fake"
    label = "Fake executor"

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
                    "write_files": True,
                    "required_artifacts": ["src/feature.py"],
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


class EditingReviewer(FakeAgent):
    """A reviewer that quietly edits product code instead of reporting."""

    name = "editing"
    label = "Editing fake"

    def run(self, run_id, role, worktree, prompt, session_id, on_event):
        (Path(worktree) / "README.md").write_text("silently rewritten\n", encoding="utf-8")
        result = super().run(run_id, role, worktree, prompt, session_id, on_event)
        result.final["proposed_tasks"] = []
        result.final["tests"] = ["pytest: pass"]
        return result


class NewFileReviewer(FakeAgent):
    """A reviewer that creates a new tracked candidate instead of reporting."""

    name = "new-file-reviewer"
    label = "New-file fake"

    def run(self, run_id, role, worktree, prompt, session_id, on_event):
        (Path(worktree) / "reviewer_added.py").write_text(
            "print('review edit')\n", encoding="utf-8"
        )
        result = super().run(run_id, role, worktree, prompt, session_id, on_event)
        result.final["proposed_tasks"] = []
        return result


class ApprovalResumeAgent(FakeAgent):
    def __init__(self):
        self.session_ids = []

    @staticmethod
    def proposal(title, dependencies=()):
        return {
            "title": title,
            "description": "Deliver %s" % title,
            "role": "implementer",
            "depends_on_titles": list(dependencies),
            "write_files": True,
            "required_artifacts": ["deliverables/%s.md" % title.lower().replace(" ", "-")],
            "requires_approval": False,
            "auto_start": False,
        }

    def run(self, run_id, role, worktree, prompt, session_id, on_event):
        self.session_ids.append(session_id)
        first = len(self.session_ids) == 1
        proposals = [self.proposal("Implement child")]
        if not first:
            proposals.append(self.proposal("Verify child", ["Implement child"]))
        return AgentRunResult(
            0,
            "complete",
            {
                "outcome": "needs_approval" if first else "completed",
                "summary": "Approve the plan" if first else "Plan complete",
                "handoff_notes": "",
                "tests": [],
                "proposed_tasks": proposals,
                "messages": [],
                "recommended_stage": "waiting_approval" if first else "done",
                "approval_question": "Approve?" if first else "",
            },
            "resumable-session",
            {},
            "",
            ["fake"],
        )


class ArtifactAgent(FakeAgent):
    def __init__(self, create_artifact):
        self.create_artifact = create_artifact

    def run(self, run_id, role, worktree, prompt, session_id, on_event):
        if self.create_artifact:
            path = Path(worktree) / "deliverables" / "spec.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("verified deliverable\n", encoding="utf-8")
        result = super().run(run_id, role, worktree, prompt, session_id, on_event)
        result.final["proposed_tasks"] = []
        return result


class UnavailableAgent(FakeAgent):
    name = "offline"
    label = "Offline executor"

    def preflight(self):
        return PreflightResult(False, "", "", ["Offline CLI not found"])


class ExhaustedAgent(FakeAgent):
    name = "exhausted"
    label = "Exhausted executor"

    def quota_snapshot(self, force=False):
        return QuotaSnapshot(
            executor=self.name,
            windows=(
                QuotaWindow(
                    "weekly",
                    100,
                    resets_at=int(time.time()) + 3600,
                    reached=True,
                ),
            ),
            source="test",
            confidence="high",
        )

    def model_for(self, tier):
        return "fake-%s" % tier


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        repo = make_git_repo(root)
        self.repo = repo
        self.db = Database(root / "db.sqlite3")
        self.db.initialize()
        self.project = self.db.create_project("Demo", str(repo), "main")
        self.git = GitService(root / "worktrees")
        self.agents = AgentRegistry([FakeAgent()], "fake")
        self.scheduler = TaskScheduler(self.db, self.git, self.agents, max_workers=1)
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

    def test_complete_write_approval_fast_forwards_main_before_marking_done(self):
        task = self.db.create_task(
            self.project["id"],
            "Ship approved feature",
            role="implementer",
            requires_approval=True,
        )
        worktree = self.git.prepare_worktree(
            self.project["id"],
            task["id"],
            task["title"],
            str(self.repo),
            "main",
        )
        path = Path(worktree["worktree_path"])
        (path / "feature.txt").write_text("approved feature\n", encoding="utf-8")
        task_head = self.git.commit_changes(str(path), task["id"], task["title"])
        self.db.update_task(
            task["id"],
            status="waiting_approval",
            branch_name=worktree["branch_name"],
            worktree_path=worktree["worktree_path"],
        )
        self.db.create_approval(task["id"], "Approve merge?", kind="complete")

        self.scheduler.resolve_approval(task["id"], True, "Approved")

        self.assertEqual(self.db.get_task(task["id"])["status"], "done")
        self.assertEqual(
            subprocess.run(
                ["git", "rev-parse", "main"],
                cwd=str(self.repo),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            task_head,
        )
        self.assertEqual(
            (self.repo / "feature.txt").read_text(encoding="utf-8"),
            "approved feature\n",
        )
        events = self.db.list_events(task["id"])
        self.assertIn("git.fast_forwarded", [event["type"] for event in events])

    def test_failed_fast_forward_keeps_human_approval_pending(self):
        task = self.db.create_task(
            self.project["id"],
            "Protect operator changes",
            role="implementer",
            requires_approval=True,
        )
        worktree = self.git.prepare_worktree(
            self.project["id"],
            task["id"],
            task["title"],
            str(self.repo),
            "main",
        )
        path = Path(worktree["worktree_path"])
        (path / "feature.txt").write_text("approved feature\n", encoding="utf-8")
        self.git.commit_changes(str(path), task["id"], task["title"])
        self.db.update_task(
            task["id"],
            status="waiting_approval",
            branch_name=worktree["branch_name"],
            worktree_path=worktree["worktree_path"],
        )
        self.db.create_approval(task["id"], "Approve merge?", kind="complete")
        (self.repo / "README.md").write_text("operator edit\n", encoding="utf-8")

        with self.assertRaisesRegex(GitError, "uncommitted changes"):
            self.scheduler.resolve_approval(task["id"], True, "Approved")

        self.assertEqual(self.db.get_task(task["id"])["status"], "waiting_approval")
        self.assertEqual(self.db.pending_approval(task["id"])["status"], "pending")
        self.assertFalse((self.repo / "feature.txt").exists())

    def test_approval_resume_reuses_children_and_resolves_new_dependencies(self):
        self.scheduler.stop()
        agent = ApprovalResumeAgent()
        scheduler = TaskScheduler(
            self.db,
            self.git,
            AgentRegistry([agent], "fake"),
            max_workers=1,
        )
        scheduler.start()
        try:
            parent = self.db.create_task(
                self.project["id"],
                "Plan with resume",
                role="coordinator",
                allow_delegation=True,
            )
            scheduler.submit(parent["id"])
            waiting = self.wait_for(parent["id"], "waiting_approval")
            self.assertEqual(len(waiting["children"]), 1)
            original_id = waiting["children"][0]["id"]

            scheduler.resolve_approval(parent["id"], True, "Approved")
            self.wait_for(parent["id"], "done")
            children = {
                child["title"]: self.db.get_task(child["id"])
                for child in self.db.get_task(parent["id"])["children"]
            }
            self.assertEqual(set(children), {"Implement child", "Verify child"})
            self.assertEqual(children["Implement child"]["id"], original_id)
            self.assertEqual(
                {item["id"] for item in children["Verify child"]["dependencies"]},
                {parent["id"], original_id},
            )
            delegated = [
                event for event in self.db.list_events(parent["id"])
                if event["type"] == "tasks.delegated"
            ]
            self.assertEqual(len(delegated), 2)
            self.assertEqual(len(delegated[1]["payload"]["created"]), 1)
            self.assertEqual(len(delegated[1]["payload"]["reused"]), 1)
            self.assertEqual(agent.session_ids, [None, "resumable-session"])
        finally:
            scheduler.stop()

    def test_duplicate_delegation_titles_fail_before_any_child_is_created(self):
        parent = self.db.create_task(
            self.project["id"],
            "Reject duplicates",
            role="coordinator",
            allow_delegation=True,
        )
        proposals = [
            ApprovalResumeAgent.proposal("Same child"),
            ApprovalResumeAgent.proposal("  SAME   child "),
        ]
        with self.assertRaisesRegex(ValueError, "重复任务标题"):
            self.scheduler._apply_delegation(parent, proposals)
        self.assertEqual(self.db.get_task(parent["id"])["children"], [])

    def test_role_write_contract_fails_before_any_child_is_created(self):
        parent = self.db.create_task(
            self.project["id"],
            "Reject role mismatch",
            role="coordinator",
            allow_delegation=True,
        )
        valid = ApprovalResumeAgent.proposal("Valid child")
        invalid = {
            "title": "Planner writes spec",
            "description": "Create a specification file",
            "role": "planner",
            "depends_on_titles": [],
            "write_files": True,
            "required_artifacts": ["docs/spec.md"],
            "requires_approval": False,
            "auto_start": False,
        }
        with self.assertRaisesRegex(ValueError, "不能承担写文件任务"):
            self.scheduler._apply_delegation(parent, [valid, invalid])
        self.assertEqual(self.db.get_task(parent["id"])["children"], [])

    def test_completed_task_missing_required_artifact_fails_closed(self):
        self.scheduler.stop()
        scheduler = TaskScheduler(
            self.db,
            self.git,
            AgentRegistry([ArtifactAgent(False)], "fake"),
            max_workers=1,
        )
        scheduler.start()
        try:
            task = self.db.create_task(
                self.project["id"],
                "Deliver a spec",
                role="implementer",
                required_artifacts=["deliverables/spec.md"],
            )
            scheduler.submit(task["id"])
            failed = self.wait_for(task["id"], "failed")
            self.assertIn("必需交付文件验收失败", failed["error"])
            self.assertIn("deliverables/spec.md", failed["error"])
            event_types = [
                event["type"] for event in self.db.list_events(task["id"])
            ]
            self.assertIn("artifact.validation_failed", event_types)
            self.assertNotIn("task.completed", event_types)
        finally:
            scheduler.stop()

    def test_completed_task_with_nonempty_required_artifact_can_finish(self):
        self.scheduler.stop()
        scheduler = TaskScheduler(
            self.db,
            self.git,
            AgentRegistry([ArtifactAgent(True)], "fake"),
            max_workers=1,
        )
        scheduler.start()
        try:
            task = self.db.create_task(
                self.project["id"],
                "Deliver a real spec",
                role="implementer",
                required_artifacts=["deliverables/spec.md"],
            )
            scheduler.submit(task["id"])
            done = self.wait_for(task["id"], "done")
            self.assertEqual(done["status"], "done")
            event_types = [
                event["type"] for event in self.db.list_events(task["id"])
            ]
            self.assertIn("artifact.validation_passed", event_types)
        finally:
            scheduler.stop()

    def test_monitor_does_not_repeat_an_agent_reported_block(self):
        task = self.db.create_task(self.project["id"], "Needs external service")
        self.db.update_task(
            task["id"], status="blocked", error="External service unavailable"
        )

        time.sleep(1.8)

        self.assertEqual(self.db.get_task(task["id"])["status"], "blocked")
        self.assertEqual(self.db.list_runs(task["id"]), [])

    def test_exhausted_auto_task_waits_for_refresh_instead_of_failing(self):
        self.scheduler.stop()
        agents = AgentRegistry([ExhaustedAgent()], "exhausted")
        scheduler = TaskScheduler(self.db, self.git, agents, max_workers=1)
        scheduler.start()
        try:
            task = self.db.create_task(
                self.project["id"], "Wait for quota", auto_start=True
            )
            self.db.update_task(task["id"], status="ready")
            time.sleep(1.8)
            waiting = self.db.get_task(task["id"])
            self.assertEqual(waiting["status"], "ready")
            self.assertEqual(self.db.list_runs(task["id"]), [])
            events = self.db.list_events(task["id"])
            self.assertIn("task.quota_deferred", [event["type"] for event in events])
            with self.assertRaises(QuotaDeferred):
                scheduler.submit(task["id"])
        finally:
            scheduler.stop()

    def test_review_gate_cannot_be_requeued_as_ordinary_work(self):
        task = self.db.create_task(self.project["id"], "Await human review")
        self.db.update_task(task["id"], status="review")
        with self.assertRaisesRegex(RuntimeError, "只有待处理或可执行"):
            self.scheduler.submit(task["id"])
        unchanged = self.db.get_task(task["id"])
        self.assertEqual(unchanged["status"], "review")
        self.assertFalse(unchanged["queued"])

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

    def test_task_runs_on_its_own_executor_and_children_inherit_it(self):
        second = FakeAgent()
        second.name = "fake-2"
        second.label = "Second fake"
        agents = AgentRegistry([FakeAgent(), second], "fake")
        scheduler = TaskScheduler(self.db, self.git, agents, max_workers=1)
        scheduler.start()
        try:
            parent = self.db.create_task(
                self.project["id"],
                "Plan on the second executor",
                role="coordinator",
                executor="fake-2",
                allow_delegation=True,
            )
            self.assertEqual(scheduler.executor_for(parent["id"]), "fake-2")
            scheduler.submit(parent["id"])
            end = time.time() + 5
            while time.time() < end and self.db.get_task(parent["id"])["status"] != "done":
                time.sleep(0.05)
            parent = self.db.get_task(parent["id"])
            self.assertEqual(parent["status"], "done")
            child = self.db.get_task(parent["children"][0]["id"])
            self.assertEqual(child["executor"], "fake-2")
        finally:
            scheduler.stop()

    def test_project_default_executor_applies_when_the_task_has_none(self):
        project = self.db.create_project(
            "Defaulted", self.project["repo_path"], "main", default_executor="fake"
        )
        task = self.db.create_task(project["id"], "Inherit the project default")
        self.assertEqual(self.scheduler.executor_for(task["id"]), "fake")

    def test_submitting_a_task_whose_executor_is_missing_reports_why(self):
        agents = AgentRegistry([FakeAgent(), UnavailableAgent()], "fake")
        scheduler = TaskScheduler(self.db, self.git, agents, max_workers=1)
        scheduler.start()
        try:
            task = self.db.create_task(
                self.project["id"], "Needs the offline CLI", executor="offline"
            )
            with self.assertRaises(RuntimeError) as caught:
                scheduler.submit(task["id"])
            self.assertIn("offline", str(caught.exception))
            self.assertIn("Offline CLI not found", str(caught.exception))
            # The healthy executor still works.
            healthy = self.db.create_task(self.project["id"], "Runs fine")
            self.assertTrue(scheduler.submit(healthy["id"]))
        finally:
            scheduler.stop()

    def test_auto_start_failure_marks_the_task_instead_of_killing_the_monitor(self):
        # Only one monitor may watch this database, or the default scheduler
        # would claim the auto-start task before the one under test sees it.
        self.scheduler.stop()
        agents = AgentRegistry([FakeAgent(), UnavailableAgent()], "fake")
        scheduler = TaskScheduler(self.db, self.git, agents, max_workers=1)
        scheduler.start()
        try:
            broken = self.db.create_task(
                self.project["id"],
                "Auto task on a missing executor",
                executor="offline",
                auto_start=True,
            )
            self.db.update_task(broken["id"], status="ready")
            end = time.time() + 5
            while time.time() < end and self.db.get_task(broken["id"])["status"] != "failed":
                time.sleep(0.05)
            failed = self.db.get_task(broken["id"])
            self.assertEqual(failed["status"], "failed")
            self.assertIn("Offline CLI not found", failed["error"])

            # The monitor survived: a healthy auto task still gets picked up.
            healthy = self.db.create_task(
                self.project["id"], "Healthy auto task", auto_start=True
            )
            self.db.update_task(healthy["id"], status="ready")
            end = time.time() + 5
            while time.time() < end and self.db.get_task(healthy["id"])["status"] != "done":
                time.sleep(0.05)
            self.assertEqual(self.db.get_task(healthy["id"])["status"], "done")
        finally:
            scheduler.stop()

    def test_reviewer_edits_are_rejected_and_never_committed(self):
        self.scheduler.stop()
        agents = AgentRegistry([EditingReviewer()], "editing")
        scheduler = TaskScheduler(self.db, self.git, agents, max_workers=1)
        scheduler.start()
        try:
            task = self.db.create_task(
                self.project["id"], "Independent review", role="reviewer"
            )
            scheduler.submit(task["id"])
            end = time.time() + 5
            while time.time() < end and self.db.get_task(task["id"])["status"] != "failed":
                time.sleep(0.05)
            failed = self.db.get_task(task["id"])
            self.assertEqual(failed["status"], "failed")
            self.assertIn("README.md", failed["error"])
            self.assertIn("独立评审契约", failed["error"])
            # The report survives so a human can read what the reviewer claimed.
            self.assertEqual(failed["summary"], "Plan created")
            # Nothing was committed onto the branch.
            worktree = failed["worktree_path"]
            log = subprocess.run(
                ["git", "log", "--oneline", "main..HEAD"],
                cwd=worktree,
                capture_output=True,
                text=True,
            )
            self.assertEqual(log.stdout.strip(), "")
        finally:
            scheduler.stop()

    def test_reviewer_new_files_are_rejected_and_never_committed(self):
        self.scheduler.stop()
        agents = AgentRegistry([NewFileReviewer()], "new-file-reviewer")
        scheduler = TaskScheduler(self.db, self.git, agents, max_workers=1)
        scheduler.start()
        try:
            task = self.db.create_task(
                self.project["id"], "Review without adding files", role="reviewer"
            )
            scheduler.submit(task["id"])
            end = time.time() + 5
            while time.time() < end and self.db.get_task(task["id"])["status"] != "failed":
                time.sleep(0.05)
            failed = self.db.get_task(task["id"])
            self.assertEqual(failed["status"], "failed")
            self.assertIn("修改或新增", failed["error"])
            log = subprocess.run(
                ["git", "log", "--oneline", "main..HEAD"],
                cwd=failed["worktree_path"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(log.stdout.strip(), "")
        finally:
            scheduler.stop()

    def test_an_implementer_may_still_commit_its_changes(self):
        self.scheduler.stop()
        agents = AgentRegistry([EditingReviewer()], "editing")
        scheduler = TaskScheduler(self.db, self.git, agents, max_workers=1)
        scheduler.start()
        try:
            task = self.db.create_task(
                self.project["id"], "Implement something", role="implementer"
            )
            scheduler.submit(task["id"])
            end = time.time() + 5
            while time.time() < end and self.db.get_task(task["id"])["status"] != "done":
                time.sleep(0.05)
            done = self.db.get_task(task["id"])
            self.assertEqual(done["status"], "done")
            log = subprocess.run(
                ["git", "log", "--oneline", "main..HEAD"],
                cwd=done["worktree_path"],
                capture_output=True,
                text=True,
            )
            self.assertIn("Implement something", log.stdout)
        finally:
            scheduler.stop()

    def test_reported_checks_are_stored_for_the_human_gate(self):
        self.scheduler.stop()
        agents = AgentRegistry([EditingReviewer()], "editing")
        scheduler = TaskScheduler(self.db, self.git, agents, max_workers=1)
        scheduler.start()
        try:
            task = self.db.create_task(self.project["id"], "Do work", role="implementer")
            scheduler.submit(task["id"])
            end = time.time() + 5
            while time.time() < end and self.db.get_task(task["id"])["status"] != "done":
                time.sleep(0.05)
            self.assertEqual(
                json.loads(self.db.get_task(task["id"])["evidence"]), ["pytest: pass"]
            )
        finally:
            scheduler.stop()

    def test_an_agent_reporting_no_checks_leaves_an_empty_evidence_list(self):
        task = self.db.create_task(self.project["id"], "No checks", role="implementer")
        self.scheduler.submit(task["id"])
        self.wait_for(task["id"], "done")
        # FakeAgent reports tests=[]; the gate must show an explicit empty list
        # rather than silently omitting the field.
        self.assertEqual(json.loads(self.db.get_task(task["id"])["evidence"]), [])
        assigned = self.db.get_task(task["id"])
        self.assertEqual(assigned["assigned_executor"], "fake")
        event_types = [item["type"] for item in self.db.list_events(task["id"])]
        self.assertIn("task.scheduled", event_types)

    def test_reviewer_children_are_assigned_a_different_executor(self):
        second = FakeAgent()
        second.name = "fake-2"
        second.label = "Second fake"
        agents = AgentRegistry([FakeAgent(), second], "fake")
        scheduler = TaskScheduler(self.db, self.git, agents, max_workers=1)
        parent = {"executor": "fake", "project_id": self.project["id"]}
        self.assertEqual(scheduler._child_executor(parent, "reviewer"), "fake-2")
        self.assertEqual(scheduler._child_executor(parent, "implementer"), "fake")
        self.assertEqual(scheduler._child_executor(parent, "qa"), "fake")

    def test_cross_review_falls_back_when_there_is_only_one_executor(self):
        parent = {"executor": "fake", "project_id": self.project["id"]}
        self.assertEqual(self.scheduler._child_executor(parent, "reviewer"), "fake")

    def test_cross_review_can_be_switched_off(self):
        second = FakeAgent()
        second.name = "fake-2"
        agents = AgentRegistry([FakeAgent(), second], "fake")
        scheduler = TaskScheduler(
            self.db, self.git, agents, max_workers=1, cross_review=False
        )
        parent = {"executor": "fake", "project_id": self.project["id"]}
        self.assertEqual(scheduler._child_executor(parent, "reviewer"), "fake")


if __name__ == "__main__":
    unittest.main()
