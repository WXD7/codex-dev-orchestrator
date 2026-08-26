from __future__ import annotations

import json
import unittest
from pathlib import Path

from orchestrator.governance import GovernanceEngine
from tests.test_governance import technology_research_for


ROOT = Path(__file__).resolve().parent.parent


class LegalBillingV2ContractTests(unittest.TestCase):
    def setUp(self):
        source = json.loads(
            (ROOT / "examples" / "legal-billing-contract-source.json").read_text(
                encoding="utf-8"
            )
        )
        self.engine = GovernanceEngine()
        intent_source = json.loads(
            (ROOT / "examples" / "legal-billing-intent-source.json").read_text(
                encoding="utf-8"
            )
        )
        research = technology_research_for(self.engine, race_recommended=True)
        intent_source["technology_research"] = research
        intent_source["technology_strategy"] = {
            "mode": "bounded_race",
            "selected_path_ids": ["path-a", "path-b"],
            "decision_rationale": "Compare two viable legal-billing integration paths under one frozen test suite.",
            "common_test_commands": ["python3 -m unittest discover -s tests"],
            "evaluation_dimensions": [
                "correctness",
                "auditability",
                "integration_cost",
                "security",
            ],
            "time_budget_minutes": 120,
            "cost_budget": "No external paid calls in automated tests",
            "fusion_allowed": True,
            "stop_conditions": ["Stop when the time budget expires", "Reject any path that fails the golden amount cases"],
        }
        for choice in intent_source["technical_choices"]:
            choice["research_hash"] = research["research_hash"]
        brief = self.engine.compile_intent_brief(intent_source)
        coverage = [
            {
                "requirement_id": "outcome-%d" % (index + 1),
                "status": "covered",
                "evidence": "Mapped to the legal billing outcomes and acceptance criteria",
            }
            for index, _item in enumerate(brief["expected_outcomes"])
        ]
        coverage.extend(
            {
                "requirement_id": item["id"],
                "status": "covered",
                "evidence": "Mapped to a deterministic golden or missing-fact case",
            }
            for item in brief["acceptance_examples"]
        )
        coverage.extend(
            {
                "requirement_id": item["id"],
                "status": "covered",
                "evidence": "Bound by the intent brief carried in the compiled contract",
            }
            for item in brief["technical_choices"]
        )
        coverage.extend(
            [
                {
                    "requirement_id": "development-executor",
                    "status": "covered",
                    "evidence": "Codex is only the locally authenticated development executor",
                },
                {
                    "requirement_id": "product-runtime",
                    "status": "covered",
                    "evidence": "DeepSeek official API is separately declared as the product runtime",
                },
                {
                    "requirement_id": "research-recommendation",
                    "status": "covered",
                    "evidence": "The selected legal-billing paths bind the frozen research hash",
                },
                {
                    "requirement_id": "technology-strategy",
                    "status": "covered",
                    "evidence": "The human-facing brief freezes a two-path race, budget, tests, dimensions, and fusion permission",
                },
            ]
        )
        inspection = self.engine.compile_intent_inspection(
            {
                "brief": brief,
                "proposed_contract_source": source,
                "technology_research": research,
                "research_evidence": [
                    "Compared the original requirement, legal billing replay, runtime split, and amount examples"
                ],
                "evidence_inputs": [
                    "original_request",
                    "intent_brief",
                    "technical_research",
                    "proposed_contract",
                    "acceptance_examples",
                ],
                "inspector_declaration": {
                    "context_id": "legal-billing-intent-inspector",
                    "fresh_context": True,
                    "read_only": True,
                    "owner_transcript_visible": False,
                    "peer_findings_visible": False,
                },
                "coverage": coverage,
                "findings": [],
                "verdict": "PASS",
            }
        )
        source["intent_alignment"] = {"brief": brief, "inspection": inspection}
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

    def test_intent_keeps_codex_development_and_deepseek_product_runtime_separate(self):
        alignment = self.contract["intent_alignment"]
        self.assertEqual(alignment["status"], "pass_pending_human_attestation")
        self.assertEqual(
            alignment["brief"]["development_executor"]["provider"], "Codex"
        )
        self.assertEqual(
            alignment["brief"]["product_runtime"]["provider"],
            "DeepSeek official API",
        )
        self.assertIn(
            "119.00 EUR",
            alignment["brief"]["acceptance_examples"][0]["expected_output"],
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
