from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from orchestrator.governed_task import (
    RUN_EVENT_MARKER,
    STAGES,
    GovernedTaskLoop,
    ProgramGateRunner,
    RunJournal,
    parse_verifier_result,
)
from orchestrator.lobehub import CommandResult
from orchestrator.quota import QuotaSnapshot


class FakeLobeHub:
    def __init__(self):
        self.tasks = {}
        self.topics = {}
        self.calls = []
        self.next_message = 0

    def json(self, arguments):
        words = list(arguments)
        self.calls.append(words + ["--json"])
        if words[:2] == ["task", "list"]:
            return list(self.tasks.values())
        if words[:2] == ["task", "view"]:
            return self.tasks.get(words[2], {})
        if words[:3] == ["task", "topic", "list"]:
            return []
        if words[:3] == ["task", "topic", "view"]:
            return list(self.topics.get(words[4], {}).get("messages", []))
        if words[:2] == ["topic", "list"]:
            return [
                {"id": topic_id, "title": topic["title"]}
                for topic_id, topic in self.topics.items()
            ]
        if words[:2] == ["message", "create"]:
            self.next_message += 1
            message_id = "msg_%d" % self.next_message
            topic_id = words[words.index("--topic-id") + 1]
            message = {
                "id": message_id,
                "role": words[words.index("--role") + 1],
                "content": words[words.index("--content") + 1],
            }
            self.topics[topic_id]["messages"].append(message)
            return {"id": message_id}
        return []

    def run(self, arguments, check=False, input_text=None, timeout=120):
        words = list(arguments)
        self.calls.append(words)
        output = "ok\n"
        if words[:3] == ["project", "task", "create"]:
            task_id = "tsk_native"
            self.tasks[task_id] = {
                "id": task_id,
                "identifier": "DEV-1",
                "name": words[words.index("--name") + 1],
                "instruction": words[words.index("--instruction") + 1],
                "activities": [],
                "status": "backlog",
            }
            output = "Created task %s\n" % task_id
        elif words[:2] == ["topic", "create"]:
            topic_id = "tpc_%d" % (len(self.topics) + 1)
            self.topics[topic_id] = {
                "title": words[words.index("--title") + 1],
                "messages": [],
            }
            output = "Created topic %s\n" % topic_id
        elif words[:2] == ["task", "comment"]:
            task_id = words[2]
            self.tasks[task_id]["activities"].append(
                {"type": "comment", "content": words[words.index("--message") + 1]}
            )
        elif words[:2] == ["task", "edit"]:
            task_id = words[2]
            if "--status" in words:
                self.tasks[task_id]["status"] = words[words.index("--status") + 1]
        elif words[:2] == ["message", "edit"]:
            message_id = words[2]
            content = words[words.index("--content") + 1]
            for topic in self.topics.values():
                for message in topic["messages"]:
                    if message["id"] == message_id:
                        message["content"] = content
        return CommandResult(0, output, "")


class PassingGates(ProgramGateRunner):
    def __init__(self, fail_first=False):
        self.calls = 0
        self.fail_first = fail_first

    def run(self, gate, repo):
        self.calls += 1
        failed = self.fail_first and self.calls == 1
        return {
            "id": gate["id"],
            "status": "failed" if failed else "passed",
            "returncode": 1 if failed else 0,
            "duration_seconds": 0.01,
            "argv": list(gate["argv"]),
            "output_sha256": "0" * 64,
            "redacted_output_tail": "failure" if failed else "ok",
            "error": "",
            "acceptance_check": False,
        }


class AlwaysFailGates(PassingGates):
    def run(self, gate, repo):
        result = super().run(gate, repo)
        result.update({"status": "failed", "returncode": 1, "redacted_output_tail": "still failing"})
        return result


class FakeTurnExecutor:
    def __init__(self, fail_after_first_visible_result=False):
        self.calls = []
        self.fail_after_first_visible_result = fail_after_first_visible_result
        self.failed = False

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        prompt = kwargs["prompt"]
        if kwargs["sandbox"] == "read-only":
            lane = prompt.split("Lane: ", 1)[1].splitlines()[0]
            final = (
                '<governance-findings>{"lane_id":"%s","status":"passed",'
                '"findings":[]}</governance-findings>' % lane
            )
        else:
            final = "repair complete" if "ONE CONSOLIDATED REPAIR" in prompt else "owner complete"
        kwargs["client"].run(
            ["message", "edit", kwargs["assistant_message_id"], "--content", final],
            check=True,
        )
        if self.fail_after_first_visible_result and not self.failed:
            self.failed = True
            raise RuntimeError("simulated local crash after visible persistence")
        return {
            "assistant_message_id": kwargs["assistant_message_id"],
            "continuation_session_id": "session_%d" % len(self.calls),
            "event_count": 3,
            "final_text": final,
        }


def quota_policy():
    return {
        "mode": "balanced",
        "models": {"owner": "gpt-5.6-terra", "verifier": "gpt-5.6-sol"},
        "reason": "test",
        "defer_until": None,
        "quota": QuotaSnapshot.unknown("codex").to_dict(),
    }


class GovernedTaskLoopTests(unittest.TestCase):
    def make_repo(self, root: Path):
        (root / ".git").mkdir()
        (root / "tests").mkdir()
        (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    def spec(self, repo: Path):
        return {
            "schema_version": 1,
            "project_id": "prj_native",
            "name": "Governed demo",
            "repo": str(repo),
            "goal": {
                "goal": "Create an observable local demo",
                "user_outcome": "The operator can run the demo and see its version",
                "acceptance_criteria": ["Running the demo prints its version"],
                "change_surfaces": ["cli"],
                "observability_signals": ["CLI success and failure output"],
                "risk": "low",
            },
            "program_gates": [
                {"id": "unit", "argv": ["python3", "-m", "unittest"], "timeout": 60}
            ],
        }

    def build_loop(self, journal, fake, executor, gates):
        return GovernedTaskLoop(
            journal,
            client=fake,
            quota_provider=quota_policy,
            turn_executor=executor,
            gate_runner=gates,
        )

    def test_nine_stages_complete_and_terminal_resume_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            self.make_repo(repo)
            journal = RunJournal.create(root / "runs", self.spec(repo), run_id="run_normal")
            fake = FakeLobeHub()
            executor = FakeTurnExecutor()
            loop = self.build_loop(journal, fake, executor, PassingGates())
            first = loop.run()
            event_count = len(journal.data["events"])
            call_count = len(executor.calls)
            second = loop.run()

        self.assertEqual(first["status"], "awaiting_human_acceptance")
        self.assertEqual(second["status"], "awaiting_human_acceptance")
        self.assertTrue(all(value in {"completed", "skipped"} for value in first["stages"].values()))
        self.assertEqual(len(STAGES), 9)
        self.assertEqual(len(executor.calls), call_count)
        self.assertEqual(len(journal.data["events"]), event_count)
        comments = [
            activity["content"]
            for activity in fake.tasks["tsk_native"]["activities"]
            if RUN_EVENT_MARKER in activity["content"]
        ]
        self.assertEqual(len(comments), len(journal.data["events"]))
        self.assertTrue(all(event["lobehub_synced"] for event in journal.data["events"]))
        self.assertEqual(fake.tasks["tsk_native"]["status"], "paused")

    def test_visible_owner_result_is_recovered_without_duplicate_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            self.make_repo(repo)
            journal = RunJournal.create(root / "runs", self.spec(repo), run_id="run_recover")
            fake = FakeLobeHub()
            executor = FakeTurnExecutor(fail_after_first_visible_result=True)
            loop = self.build_loop(journal, fake, executor, PassingGates())
            interrupted = loop.run()
            resumed = loop.run()

        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(interrupted["current_stage"], STAGES[4])
        self.assertEqual(resumed["status"], "awaiting_human_acceptance")
        owner_writes = [call for call in executor.calls if call["sandbox"] == "workspace-write"]
        self.assertEqual(len(owner_writes), 1)
        owner_turn = journal.data["stages"][STAGES[4]]["output"]["turns"]["owner_delivery"]
        self.assertTrue(owner_turn["recovered_from_lobehub"])

    def test_program_failure_triggers_exactly_one_repair_and_full_recheck(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            self.make_repo(repo)
            journal = RunJournal.create(root / "runs", self.spec(repo), run_id="run_repair")
            fake = FakeLobeHub()
            executor = FakeTurnExecutor()
            gates = PassingGates(fail_first=True)
            summary = self.build_loop(journal, fake, executor, gates).run()

        self.assertEqual(summary["status"], "awaiting_human_acceptance")
        self.assertEqual(summary["repair_rounds_used"], 1)
        writes = [call for call in executor.calls if call["sandbox"] == "workspace-write"]
        self.assertEqual(len(writes), 2)
        self.assertIn("ONE CONSOLIDATED REPAIR", writes[-1]["prompt"])
        self.assertEqual(journal.data["stages"][STAGES[6]]["status"], "skipped")
        self.assertEqual(journal.data["stages"][STAGES[7]]["output"]["aggregate"]["decision"], "pass")

    def test_requested_checkpoint_resumes_without_rerunning_completed_stages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            self.make_repo(repo)
            journal = RunJournal.create(root / "runs", self.spec(repo), run_id="run_checkpoint")
            fake = FakeLobeHub()
            executor = FakeTurnExecutor()
            loop = self.build_loop(journal, fake, executor, PassingGates())
            stopped = loop.run(stop_after=STAGES[3])
            resumed = loop.run()

        self.assertEqual(stopped["status"], "checkpointed")
        self.assertEqual(resumed["status"], "awaiting_human_acceptance")
        self.assertEqual(journal.data["stages"][STAGES[0]]["attempts"], 1)
        self.assertEqual(journal.data["stages"][STAGES[3]]["attempts"], 1)

    def test_high_risk_run_requires_explicit_operator_decision_before_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            self.make_repo(repo)
            spec = self.spec(repo)
            spec["goal"].update(
                {
                    "risk": "high",
                    "security_sensitive": True,
                    "security_boundaries": ["untrusted request to authenticated session"],
                }
            )
            journal = RunJournal.create(root / "runs", spec, run_id="run_high_risk")
            fake = FakeLobeHub()
            executor = FakeTurnExecutor()
            paused = self.build_loop(journal, fake, executor, PassingGates()).run()
            calls_before_approval = len(executor.calls)
            journal.data["spec"].setdefault("decisions", {})["material_execution_approved"] = True
            journal.save()
            resumed = self.build_loop(journal, fake, executor, PassingGates()).run()

        self.assertEqual(paused["status"], "needs_human_decision")
        self.assertEqual(paused["current_stage"], STAGES[4])
        self.assertEqual(calls_before_approval, 0)
        self.assertEqual(resumed["status"], "awaiting_human_acceptance")
        self.assertTrue(any(call["sandbox"] == "workspace-write" for call in executor.calls))

    def test_second_gate_failure_escalates_without_second_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            self.make_repo(repo)
            journal = RunJournal.create(root / "runs", self.spec(repo), run_id="run_escalate")
            fake = FakeLobeHub()
            executor = FakeTurnExecutor()
            gates = AlwaysFailGates()
            first = self.build_loop(journal, fake, executor, gates).run()
            second = self.build_loop(journal, fake, executor, gates).run()

        self.assertEqual(first["status"], "needs_human_decision")
        self.assertEqual(second["status"], "needs_human_decision")
        writes = [call for call in executor.calls if call["sandbox"] == "workspace-write"]
        self.assertEqual(len(writes), 2)
        self.assertEqual(second["repair_rounds_used"], 1)

    def test_invalid_verifier_envelope_fails_closed(self):
        parsed = parse_verifier_result("looks good", "security_falsification")
        self.assertFalse(parsed["parsed"])
        self.assertEqual(parsed["status"], "blocked")

    def test_native_session_marker_with_spaces_is_recovered(self):
        task = {
            "activities": [
                {
                    "content": (
                        "[engineering-governance codex-session] session_native "
                        "topic tpc_native"
                    )
                }
            ]
        }
        self.assertEqual(
            GovernedTaskLoop._session_for_topic(task, "tpc_native"),
            "session_native",
        )

    def test_real_gate_runner_redacts_secrets_and_detects_worktree_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
            tracked = repo / "tracked.txt"
            tracked.write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=str(repo), check=True)
            secret_script = repo / "print_secret.py"
            secret_script.write_text(
                "print('API_KEY=sk-this-must-not-appear')\n", encoding="utf-8"
            )
            safe = ProgramGateRunner().run(
                {"id": "secret", "argv": [sys.executable, "print_secret.py"]}, repo
            )
            mutate_script = repo / "mutate.py"
            mutate_script.write_text(
                "from pathlib import Path\nPath('tracked.txt').write_text('after\\n')\n",
                encoding="utf-8",
            )
            mutated = ProgramGateRunner().run(
                {"id": "mutate", "argv": [sys.executable, "mutate.py"]}, repo
            )

        self.assertEqual(safe["status"], "passed")
        self.assertNotIn("sk-this-must-not-appear", safe["redacted_output_tail"])
        self.assertIn("[REDACTED]", safe["redacted_output_tail"])
        self.assertEqual(mutated["status"], "failed")
        self.assertTrue(mutated["worktree_mutated"])

    def test_inline_interpreter_program_gate_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            self.make_repo(repo)
            spec = self.spec(repo)
            spec["program_gates"] = [
                {"id": "unsafe", "argv": ["python3", "-c", "print('no')"]}
            ]
            journal = RunJournal.create(root / "runs", spec, run_id="run_unsafe_gate")
            with self.assertRaisesRegex(ValueError, "inline Python"):
                GovernedTaskLoop(journal, client=FakeLobeHub())


if __name__ == "__main__":
    unittest.main()
