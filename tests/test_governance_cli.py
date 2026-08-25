from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from orchestrator.cli import main
from tests.helpers import make_git_repo
from tests.test_governance import ready_source


class GovernanceCLITests(unittest.TestCase):
    def call_with_stdin(self, arguments, payload):
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), redirect_stdout(output):
            status = main(arguments)
        self.assertEqual(status, 0)
        return json.loads(output.getvalue())

    def test_compile_route_contexts_handoff_and_adjudicate_round_trip(self):
        contract = self.call_with_stdin(["governance", "compile"], ready_source())
        plan = self.call_with_stdin(["governance", "route"], contract)
        contexts = self.call_with_stdin(
            ["governance", "contexts"], {"contract": contract, "plan": plan}
        )
        handoff = self.call_with_stdin(
            ["governance", "handoff"], {"contract": contract, "plan": plan}
        )
        verdict = self.call_with_stdin(
            ["governance", "adjudicate"],
            {
                "contract": contract,
                "plan": plan,
                "repair_round": 0,
                "deterministic_results": [
                    {
                        "name": "unit",
                        "status": "passed",
                        "required": True,
                        "command": "python3 -m unittest",
                    }
                ],
                "findings": [],
            },
        )

        self.assertEqual(plan["contract_hash"], contract["contract_hash"])
        self.assertTrue(contexts["contexts"])
        self.assertEqual(handoff["status"], "ready_for_control_plane")
        self.assertEqual(verdict["decision"], "ready_for_final_verification")

    def test_blueprint_and_init_are_stateless_and_do_not_overwrite(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["governance", "blueprint"])
        self.assertEqual(status, 0)
        self.assertEqual(
            json.loads(output.getvalue())["default_path"]["executor"],
            "locally authenticated Codex CLI/app-server",
        )

        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            source = Path(directory) / "contract-source.json"
            source.write_text(json.dumps(ready_source()), encoding="utf-8")
            result_path = Path(directory) / "result.json"

            status = main(
                [
                    "governance",
                    "init",
                    "--target",
                    str(repo),
                    "--input",
                    str(source),
                    "--output",
                    str(result_path),
                ]
            )

            self.assertEqual(status, 0)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(result["contract_status"], "ready")
            self.assertEqual(len(result["files"]), 10)
            self.assertTrue((repo / ".ai-delivery" / "integrations.json").is_file())
            self.assertTrue((repo / ".ai-delivery" / "delivery-handoff.json").is_file())
            self.assertTrue((repo / ".ai-delivery" / "bad-case-registry.json").is_file())
            self.assertTrue((repo / ".ai-delivery" / "calibration-policy.json").is_file())
            with self.assertRaisesRegex(FileExistsError, "no files were changed"):
                main(
                    [
                        "governance",
                        "init",
                        "--target",
                        str(repo),
                        "--input",
                        str(source),
                    ]
                )

    def test_preflight_builds_a_ready_capsule_without_mutating_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            contract = self.call_with_stdin(
                ["governance", "compile"], ready_source(environment={"required_commands": ["git"]})
            )
            capsule = self.call_with_stdin(
                ["governance", "preflight"],
                {"repo": str(repo), "contract": contract},
            )

        self.assertEqual(capsule["status"], "ready")
        self.assertEqual(capsule["repository_root"], capsule["diff_root"])
        self.assertEqual(capsule["commands"][0]["name"], "git")

    def test_question_resolution_cases_and_calibration_are_pure_cli_operations(self):
        contract = self.call_with_stdin(
            ["governance", "compile"],
            ready_source(
                uncertainties=[
                    {
                        "decision_id": "choice",
                        "category": "policy_choice",
                        "statement": "Choose one policy",
                        "state": "unknown",
                        "impact": "high",
                    }
                ]
            ),
        )
        proposal = self.call_with_stdin(
            ["governance", "resolve"],
            {
                "contract": contract,
                "answers": [
                    {
                        "decision_id": "choice",
                        "answer": "Use the strict policy",
                        "answered_by": "owner",
                        "authority": "human",
                    }
                ],
            },
        )
        registry = self.call_with_stdin(
            ["governance", "cases"],
            {"registry_id": "empty", "cases": []},
        )
        profile = self.call_with_stdin(
            ["governance", "calibrate"],
            {"lane_id": "security", "evaluations": []},
        )

        self.assertEqual(proposal["proposed_contract"]["status"], "ready")
        self.assertEqual(registry["metrics"]["total"], 0)
        self.assertEqual(profile["mode"], "shadow")


if __name__ == "__main__":
    unittest.main()
