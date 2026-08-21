from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from orchestrator.codex_agent import CodexAgent


SCHEMA = {
    "type": "object",
    "properties": {"outcome": {"type": "string"}},
    "required": ["outcome"],
    "additionalProperties": False,
}


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import pathlib
import sys

args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli fake")
    raise SystemExit(0)
if args == ["login", "status"]:
    print("WARNING: harmless setup warning")
    print("Logged in using ChatGPT")
    raise SystemExit(0)
if "exec" in args:
    output = pathlib.Path(args[args.index("-o") + 1])
    final = {
        "outcome": "completed",
        "summary": "done",
        "handoff_notes": "",
        "tests": ["fake: pass"],
        "proposed_tasks": [],
        "messages": [],
        "recommended_stage": "done",
        "approval_question": ""
    }
    output.write_text(json.dumps(final), encoding="utf-8")
    print(json.dumps({"type": "thread.started", "thread_id": "fake-session"}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12}}))
    raise SystemExit(0)
raise SystemExit(2)
'''


class CodexAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binary = self.root / "codex-fake"
        self.binary.write_text(FAKE_CODEX, encoding="utf-8")
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.schema = self.root / "schema.json"
        self.schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        self.agent = CodexAgent(
            str(self.binary), self.schema, self.root / "runs", timeout_seconds=10
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_preflight_requires_chatgpt_and_run_parses_jsonl(self):
        preflight = self.agent.preflight()
        self.assertTrue(preflight.ok)
        self.assertEqual(preflight.auth_status, "Logged in using ChatGPT")
        events = []
        result = self.agent.run(
            "run_1",
            "implementer",
            self.root,
            "Do a fake task",
            None,
            lambda kind, payload: events.append(kind),
        )
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.session_id, "fake-session")
        self.assertEqual(result.final["summary"], "done")
        self.assertIn("turn.completed", events)

    def test_command_is_sandboxed_and_api_keys_are_removed(self):
        command = self.agent.build_command(
            "implementer", self.root, "prompt", self.root / "out.json"
        )
        self.assertIn("workspace-write", command)
        self.assertNotIn("danger-full-access", command)
        self.assertNotIn("--approve-for-me", command)
        old = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "must-not-leak"
        try:
            self.assertNotIn("OPENAI_API_KEY", self.agent.clean_environment())
        finally:
            if old is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old

    def test_planners_and_reviewers_are_read_only(self):
        planner = self.agent.build_command(
            "planner", self.root, "plan", self.root / "plan.json"
        )
        reviewer = self.agent.build_command(
            "reviewer", self.root, "review", self.root / "review.json"
        )
        self.assertIn("read-only", planner)
        self.assertIn("read-only", reviewer)

    def test_model_override_is_passed_to_new_and_resumed_runs(self):
        fresh = self.agent.build_command(
            "implementer",
            self.root,
            "build",
            self.root / "fresh.json",
            model="gpt-5.6-luna",
        )
        resumed = self.agent.build_command(
            "implementer",
            self.root,
            "continue",
            self.root / "resume.json",
            session_id="session-1",
            model="gpt-5.6-terra",
        )
        self.assertEqual(fresh[fresh.index("--model") + 1], "gpt-5.6-luna")
        self.assertEqual(resumed[resumed.index("--model") + 1], "gpt-5.6-terra")
        self.assertEqual(resumed[resumed.index("--sandbox") + 1], "workspace-write")
        self.assertEqual(resumed[resumed.index("--cd") + 1], str(self.root))
        self.assertLess(resumed.index("--sandbox"), resumed.index("resume"))
        self.assertLess(resumed.index("--cd"), resumed.index("resume"))


if __name__ == "__main__":
    unittest.main()
