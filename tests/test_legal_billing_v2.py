from __future__ import annotations

import json
import unittest
from pathlib import Path

from orchestrator.governance import GovernanceEngine


ROOT = Path(__file__).resolve().parent.parent


class LegalBillingV2ContractTests(unittest.TestCase):
    def setUp(self):
        source = json.loads(
            (ROOT / "examples" / "legal-billing-contract-source.json").read_text(
                encoding="utf-8"
            )
        )
        self.engine = GovernanceEngine()
        self.contract = self.engine.compile_contract(source)
        self.plan = self.engine.route(self.contract)

    def test_contract_routes_only_real_questions_to_human(self):
        self.assertEqual(self.contract["status"], "ready")
        self.assertEqual(self.contract["clarifications"], [])
        routes = {
            item["id"]: item["route"]
            for item in self.contract["question_gate"]["non_blocking_routes"]
        }
        self.assertEqual(routes["typed-correction-invariant"], "prove_from_repository")
        self.assertEqual(
            routes["kandev-runtime-shape"], "research_without_interrupting_owner"
        )

    def test_plan_carries_strong_evidence_and_old_failures_into_blind_review(self):
        self.assertEqual(
            set(self.plan["required_evidence_classes"]),
            {"property_test", "mutation_test", "browser_e2e"},
        )
        self.assertEqual(
            {item["id"] for item in self.plan["must_kill_cases"]},
            {
                "audit-replay-forgery",
                "substring-correction-evidence",
                "typed-ui-correction-roundtrip",
            },
        )
        self.assertEqual(
            self.plan["final_verifier"]["required_cases"],
            [item["id"] for item in self.plan["must_kill_cases"]],
        )

    def test_missing_mutation_property_and_browser_evidence_fails_closed(self):
        verdict = self.engine.adjudicate(
            {
                "contract": self.contract,
                "plan": self.plan,
                "repair_round": 0,
                "deterministic_results": [
                    {
                        "name": check,
                        "command": check,
                        "status": "passed",
                        "required": True,
                        "evidence": "submitted",
                    }
                    for check in self.contract["deterministic_checks"]
                ],
                "findings": [],
            }
        )
        self.assertEqual(verdict["decision"], "repair_once")
        self.assertEqual(
            {item["class"] for item in verdict["deterministic_blockers"]},
            {"property_test", "mutation_test", "browser_e2e"},
        )


if __name__ == "__main__":
    unittest.main()
