from __future__ import annotations

import unittest

from orchestrator.prompts import build_prompt


class PromptContractTests(unittest.TestCase):
    def test_prompt_exposes_artifacts_and_strict_delegation_contract(self):
        task = {
            "id": "tsk_1",
            "title": "Plan work",
            "description": "Decompose the feature",
            "role": "coordinator",
            "allow_delegation": True,
            "required_artifacts": [],
            "dependencies": [],
        }
        project = {
            "name": "Demo",
            "base_branch": "main",
            "workflow": "feature-dev",
        }

        prompt = build_prompt(task, project, [])

        self.assertIn("write_files=true", prompt)
        self.assertIn("role=implementer or role=qa", prompt)
        self.assertIn("reviewer proposals must set write_files=false", prompt)
        self.assertIn('"required_artifacts": []', prompt)

    def test_planner_is_warned_not_to_claim_file_delivery(self):
        task = {
            "id": "tsk_2",
            "title": "Write a specification",
            "description": "Create docs/spec.md",
            "role": "planner",
            "allow_delegation": False,
            "required_artifacts": [],
            "dependencies": [],
        }
        project = {
            "name": "Demo",
            "base_branch": "main",
            "workflow": "feature-dev",
        }

        prompt = build_prompt(task, project, [])

        self.assertIn("role mismatch", prompt)
        self.assertIn("read-only", prompt)


if __name__ == "__main__":
    unittest.main()
