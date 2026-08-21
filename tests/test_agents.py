from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from orchestrator.agents import AgentRegistry, UnknownExecutor, build_registry
from orchestrator.config import Config
from orchestrator.models import AgentRunResult, PreflightResult


class StubExecutor:
    def __init__(self, name, ready=True, problems=()):
        self.name = name
        self.label = name.title()
        self.ready = ready
        self.problems = list(problems)
        self.calls = []

    def preflight(self):
        return PreflightResult(self.ready, "v1", "signed in", list(self.problems))

    def run(self, run_id, role, worktree, prompt, session_id, on_event):
        self.calls.append(run_id)
        return AgentRunResult(0, "complete", {"outcome": "completed"})


class AgentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.codex = StubExecutor("codex")
        self.claude = StubExecutor("claude-code")
        self.registry = AgentRegistry([self.codex, self.claude], "codex")

    def test_resolution_prefers_task_then_project_then_default(self):
        self.assertEqual(self.registry.resolve_name(), "codex")
        self.assertEqual(
            self.registry.resolve_name(project={"default_executor": "claude-code"}),
            "claude-code",
        )
        self.assertEqual(
            self.registry.resolve_name(
                task={"executor": "codex"}, project={"default_executor": "claude-code"}
            ),
            "codex",
        )

    def test_blank_and_unknown_names_fall_back_to_the_default(self):
        self.assertEqual(self.registry.resolve_name(task={"executor": ""}), "codex")
        self.assertEqual(self.registry.resolve_name(task={"executor": "gpt-9"}), "codex")

    def test_get_rejects_an_unknown_executor(self):
        self.assertIs(self.registry.get("claude-code"), self.claude)
        self.assertIs(self.registry.get(""), self.codex)
        with self.assertRaises(UnknownExecutor):
            self.registry.get("nope")

    def test_one_broken_executor_does_not_block_the_board(self):
        broken = AgentRegistry(
            [StubExecutor("codex"), StubExecutor("claude-code", ready=False, problems=["not installed"])],
            "codex",
        )
        aggregate = broken.preflight()
        self.assertTrue(aggregate.ok)
        self.assertEqual(aggregate.problems, ["claude-code: not installed"])
        self.assertTrue(broken.check("codex").ok)
        self.assertFalse(broken.check("claude-code").ok)

    def test_no_usable_executor_is_not_ready(self):
        dead = AgentRegistry([StubExecutor("codex", ready=False, problems=["missing"])], "codex")
        self.assertFalse(dead.preflight().ok)

    def test_health_lists_every_executor(self):
        health = self.registry.health()
        self.assertEqual(health["default"], "codex")
        self.assertEqual(
            [item["name"] for item in health["executors"]], ["codex", "claude-code"]
        )
        self.assertTrue(all(item["ready"] for item in health["executors"]))

    def test_run_dispatches_to_the_named_executor(self):
        self.registry.run("run_1", "implementer", Path("."), "prompt", None, lambda *_: None, "claude-code")
        self.assertEqual(self.claude.calls, ["run_1"])
        self.assertEqual(self.codex.calls, [])
        self.registry.run("run_2", "implementer", Path("."), "prompt", None, lambda *_: None)
        self.assertEqual(self.codex.calls, ["run_2"])

    def test_registry_requires_at_least_one_executor(self):
        with self.assertRaises(ValueError):
            AgentRegistry([], "codex")


class BuildRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(data_dir=Path(self.temp.name))
        self.schema = (
            Path(__file__).resolve().parent.parent
            / "orchestrator"
            / "schemas"
            / "agent_result.schema.json"
        )
        self.config.ensure_directories()

    def tearDown(self):
        self.temp.cleanup()

    def test_default_configuration_is_codex_only(self):
        registry = build_registry(self.config, self.schema)
        self.assertEqual(registry.names, ["codex"])
        self.assertEqual(registry.default_name, "codex")

    def test_both_executors_can_be_enabled(self):
        config = replace(
            self.config, executors=("codex", "claude-code"), default_executor="claude-code"
        )
        registry = build_registry(config, self.schema)
        self.assertEqual(registry.names, ["codex", "claude-code"])
        self.assertEqual(registry.default_name, "claude-code")

    def test_unknown_executor_name_is_rejected_at_startup(self):
        config = replace(self.config, executors=("codex", "gpt-9"))
        with self.assertRaises(UnknownExecutor):
            build_registry(config, self.schema)


if __name__ == "__main__":
    unittest.main()
