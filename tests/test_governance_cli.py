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
        self.assertEqual(verdict["decision"], "ready_for_human_merge")

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
            self.assertEqual(len(result["files"]), 7)
            self.assertTrue((repo / ".ai-delivery" / "integrations.json").is_file())
            self.assertTrue((repo / ".ai-delivery" / "delivery-handoff.json").is_file())
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


if __name__ == "__main__":
    unittest.main()
