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
        self.assertTrue(self.agent.preflight().ok)
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
        old = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "must-not-leak"
        try:
            self.assertNotIn("OPENAI_API_KEY", self.agent.clean_environment())
        finally:
            if old is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()

