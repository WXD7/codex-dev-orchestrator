from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from orchestrator.agent_base import clean_environment, extract_structured_result
from orchestrator.claude_agent import (
    DISALLOWED_TOOLS,
    EMPTY_MCP_CONFIG,
    ClaudeCodeAgent,
    assert_no_forbidden_flags,
)

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "outcome",
        "summary",
        "handoff_notes",
        "tests",
        "proposed_tasks",
        "messages",
        "recommended_stage",
        "approval_question",
    ],
}

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
if args == ["--version"]:
    print("2.1.237 (Claude Code)")
    raise SystemExit(0)
if args == ["auth", "status"]:
    print(os.environ.get("FAKE_AUTH", json.dumps({
        "loggedIn": True, "authMethod": "claude.ai",
        "apiProvider": "firstParty", "subscriptionType": "pro",
    })))
    raise SystemExit(int(os.environ.get("FAKE_AUTH_EXIT", "0")))

result_text = os.environ.get("FAKE_RESULT", "{}")
is_error = os.environ.get("FAKE_IS_ERROR") == "1"
exit_code = int(os.environ.get("FAKE_EXIT", "0"))

print(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-42"}))
print(json.dumps({
    "type": "assistant",
    "session_id": "sess-42",
    "message": {"content": [{"type": "text", "text": "working"}]},
}))
event = {
    "type": "result",
    "subtype": "success",
    "session_id": "sess-42",
    "is_error": is_error,
    "result": result_text,
    "num_turns": 3,
    "permission_denials": json.loads(os.environ.get("FAKE_DENIALS", "[]")),
    "usage": {"input_tokens": 11, "output_tokens": 7},
    "total_cost_usd": 0.01,
}
if os.environ.get("FAKE_STRUCTURED"):
    event["structured_output"] = json.loads(os.environ["FAKE_STRUCTURED"])
print(json.dumps(event))
raise SystemExit(exit_code)
'''

GOOD_RESULT = {
    "outcome": "completed",
    "summary": "Added the endpoint",
    "handoff_notes": "Ready for review",
    "tests": ["python3 -m unittest: pass"],
    "proposed_tasks": [],
    "messages": [],
    "recommended_stage": "review",
    "approval_question": "",
}


class ClaudeAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binary = self.root / "claude-fake"
        self.binary.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.schema = self.root / "schema.json"
        self.schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        self.agent = ClaudeCodeAgent(
            str(self.binary), self.schema, self.root / "runs", timeout_seconds=60
        )
        self._saved_env = {}

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def set_env(self, **values):
        for key, value in values.items():
            self._saved_env.setdefault(key, os.environ.get(key))
            os.environ[key] = value

    def run_agent(self, role="implementer", session_id=None):
        events = []
        return self.agent.run(
            "run_1",
            role,
            self.root,
            "Do the task",
            session_id,
            lambda kind, payload: events.append(kind),
        ), events

    # -- preflight and command --------------------------------------------

    def test_preflight_reports_version_and_login(self):
        preflight = self.agent.preflight()
        self.assertTrue(preflight.ok, preflight.problems)
        self.assertIn("Claude Code", preflight.version)
        self.assertEqual(preflight.auth_status, "Logged in via claude.ai (pro)")

    def test_preflight_fails_when_the_cli_is_missing(self):
        missing = ClaudeCodeAgent("claude-not-installed", self.schema, self.root / "runs")
        result = missing.preflight()
        self.assertFalse(result.ok)
        self.assertIn("ORCH_CLAUDE_BINARY", result.problems[0])

    def test_preflight_fails_when_not_signed_in(self):
        self.set_env(FAKE_AUTH=json.dumps({"loggedIn": False}))
        result = self.agent.preflight()
        self.assertFalse(result.ok)
        self.assertIn("not signed in", result.problems[0])

    def test_schema_argument_strips_the_unresolvable_meta_reference(self):
        # The real CLI rejects a $schema it cannot dereference.
        argument = json.loads(self.agent.schema_argument())
        self.assertNotIn("$schema", argument)
        self.assertIn("outcome", argument["required"])

    def test_command_passes_the_schema_and_ends_option_parsing(self):
        command = self.agent.build_command("implementer", self.root, "do it")
        self.assertEqual(
            json.loads(command[command.index("--json-schema") + 1])["type"], "object"
        )
        # Variadic tool flags would otherwise swallow the prompt.
        self.assertEqual(command[-2], "--")
        self.assertTrue(command[-1].startswith("do it"))

    def test_command_isolates_the_task_from_operator_mcp_servers(self):
        command = self.agent.build_command("implementer", self.root, "do it")
        self.assertIn("--strict-mcp-config", command)
        self.assertEqual(command[command.index("--mcp-config") + 1], EMPTY_MCP_CONFIG)

    def test_read_only_roles_cannot_reach_editing_tools(self):
        planner = self.agent.build_command("planner", self.root, "plan")
        allowed = planner[planner.index("--allowedTools") + 1].split(",")
        self.assertEqual(allowed, ["Read", "Grep", "Glob"])
        self.assertNotIn("--permission-mode", planner)

        implementer = self.agent.build_command("implementer", self.root, "build")
        allowed = implementer[implementer.index("--allowedTools") + 1].split(",")
        self.assertIn("Edit", allowed)
        self.assertIn("Bash", allowed)
        self.assertEqual(
            implementer[implementer.index("--permission-mode") + 1], "acceptEdits"
        )

    def test_commands_never_bypass_permissions_or_reach_the_network(self):
        for role in ("coordinator", "planner", "implementer", "reviewer", "qa"):
            command = self.agent.build_command(role, self.root, "prompt")
            joined = " ".join(command[:-1])
            self.assertNotIn("--dangerously-skip-permissions", joined)
            self.assertNotIn("bypassPermissions", joined)
            disallowed = command[command.index("--disallowedTools") + 1].split(",")
            self.assertIn("WebFetch", disallowed)
            self.assertIn("Bash(git push:*)", disallowed)
            self.assertEqual(disallowed, list(DISALLOWED_TOOLS))

    def test_forbidden_flag_guard_raises(self):
        for flag in (
            "--dangerously-skip-permissions",
            "--allow-dangerously-skip-permissions",
            "--permission-mode=bypassPermissions",
        ):
            with self.assertRaises(ValueError, msg=flag):
                assert_no_forbidden_flags(["claude", flag, "prompt"])

    def test_resume_passes_the_session_id(self):
        command = self.agent.build_command("implementer", self.root, "again", "sess-42")
        self.assertEqual(command[command.index("--resume") + 1], "sess-42")

    def test_prompt_carries_a_short_result_contract(self):
        prompt = self.agent.build_command("implementer", self.root, "base prompt")[-1]
        self.assertIn("base prompt", prompt)
        self.assertIn("STRUCTURED RESULT", prompt)
        # The schema itself travels in --json-schema, not in the prompt.
        self.assertNotIn("approval_question", prompt)

    def test_anthropic_keys_are_stripped_from_the_child_environment(self):
        self.set_env(ANTHROPIC_API_KEY="must-not-leak", CLAUDE_CODE_OAUTH_TOKEN="nope")
        env = clean_environment()
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)

    # -- run --------------------------------------------------------------

    def test_run_prefers_the_schema_validated_structured_output(self):
        # structured_output wins even when the result text disagrees.
        self.set_env(
            FAKE_STRUCTURED=json.dumps(GOOD_RESULT),
            FAKE_RESULT="I cannot produce JSON.",
        )
        result, _ = self.run_agent()
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.final["summary"], "Added the endpoint")

    def test_run_records_permission_denials(self):
        self.set_env(
            FAKE_STRUCTURED=json.dumps(GOOD_RESULT),
            FAKE_RESULT=json.dumps(GOOD_RESULT),
            FAKE_DENIALS=json.dumps([{"tool_name": "WebFetch"}]),
        )
        result, events = self.run_agent()
        self.assertIn("claude.permission_denied", events)
        self.assertIn("denied 1 tool use", result.stderr_tail)

    def test_run_captures_session_usage_and_structured_result(self):
        self.set_env(FAKE_RESULT=json.dumps(GOOD_RESULT))
        result, events = self.run_agent()
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.session_id, "sess-42")
        self.assertEqual(result.final["summary"], "Added the endpoint")
        self.assertEqual(result.usage["input_tokens"], 11)
        self.assertEqual(result.usage["total_cost_usd"], 0.01)
        self.assertEqual(result.usage["num_turns"], 3)
        self.assertIn("claude.result", events)
        saved = json.loads((self.root / "runs" / "run_1" / "final.json").read_text())
        self.assertEqual(saved["recommended_stage"], "review")

    def test_run_recovers_a_fenced_result_wrapped_in_prose(self):
        self.set_env(
            FAKE_RESULT="Here is what I did.\n\n```json\n%s\n```\n\nLet me know."
            % json.dumps(GOOD_RESULT)
        )
        result, _ = self.run_agent()
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.final["outcome"], "completed")

    def test_run_fails_when_required_fields_are_missing(self):
        self.set_env(FAKE_RESULT=json.dumps({"outcome": "completed", "summary": "hi"}))
        result, _ = self.run_agent()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.final, {})
        self.assertIn("missing required fields", result.stderr_tail)
        self.assertIn("handoff_notes", result.stderr_tail)

    def test_run_fails_when_no_json_is_returned(self):
        self.set_env(FAKE_RESULT="I could not do it, sorry.")
        result, _ = self.run_agent()
        self.assertEqual(result.status, "failed")
        self.assertIn("did not return a JSON object", result.stderr_tail)

    def test_run_fails_when_the_cli_reports_an_error_result(self):
        self.set_env(FAKE_RESULT=json.dumps(GOOD_RESULT), FAKE_IS_ERROR="1")
        result, _ = self.run_agent()
        self.assertEqual(result.status, "failed")
        self.assertIn("reported an error result", result.stderr_tail)

    def test_run_reports_a_nonzero_exit(self):
        self.set_env(FAKE_RESULT=json.dumps(GOOD_RESULT), FAKE_EXIT="3")
        result, _ = self.run_agent()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.exit_code, 3)

    def test_persisted_command_hides_the_prompt(self):
        self.set_env(FAKE_RESULT=json.dumps(GOOD_RESULT))
        result, _ = self.run_agent()
        self.assertTrue(result.command[-1].startswith("<task prompt:"))
        self.assertNotIn("Do the task", " ".join(result.command))


class ExtractStructuredResultTests(unittest.TestCase):
    def test_plain_object(self):
        final, error = extract_structured_result('{"outcome": "completed"}')
        self.assertEqual(error, "")
        self.assertEqual(final["outcome"], "completed")

    def test_last_fenced_block_wins(self):
        text = '```json\n{"outcome": "first"}\n```\nand then\n```json\n{"outcome": "second"}\n```'
        final, _ = extract_structured_result(text)
        self.assertEqual(final["outcome"], "second")

    def test_object_with_braces_inside_strings(self):
        text = 'Result:\n{"summary": "use {curly} braces", "outcome": "completed"}\nDone.'
        final, error = extract_structured_result(text)
        self.assertEqual(error, "")
        self.assertEqual(final["summary"], "use {curly} braces")

    def test_nested_objects_are_kept_whole(self):
        text = 'x {"a": {"b": 1}, "outcome": "completed"} y'
        final, _ = extract_structured_result(text)
        self.assertEqual(final["a"], {"b": 1})

    def test_empty_and_unparseable_text(self):
        self.assertNotEqual(extract_structured_result("")[1], "")
        self.assertNotEqual(extract_structured_result("no json here")[1], "")
        self.assertNotEqual(extract_structured_result("[1, 2, 3]")[1], "")


if __name__ == "__main__":
    unittest.main()
