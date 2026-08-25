from __future__ import annotations

import unittest

from orchestrator.governance import GovernanceEngine
from orchestrator.governance_learning import (
    compile_bad_case_registry,
    compile_inspector_calibration,
)
from tests.test_governance import ready_source


def confirmed_registry():
    return compile_bad_case_registry(
        {
            "registry_id": "legal-billing-regressions",
            "cases": [
                {
                    "id": "audit-replay",
                    "title": "重算普通哈希后仍不得接受伪造审计包",
                    "category": "audit_authenticity",
                    "counterexample": "修改 actor 和 reason 后重算全部普通哈希",
                    "expected": "可信控制边界拒绝 replay",
                    "severity": "critical",
                    "status": "confirmed",
                    "source": "B 最终盲验",
                    "evidence_classes": ["semantic_oracle", "mutation_test"],
                    "confirmed_by": "human-evaluator",
                    "confirmation_evidence": "独立终验命令已复现",
                    "hidden_from_owner": True,
                }
            ],
        }
    )


def blocking_profile(lane_id="security"):
    return compile_inspector_calibration(
        {
            "lane_id": lane_id,
            "policy": {
                "minimum_cases": 2,
                "minimum_positive_cases": 1,
                "minimum_negative_cases": 1,
                "minimum_recall": 1,
                "maximum_false_positive_rate": 0,
                "minimum_human_agreement": 1,
                "minimum_independent_contributions": 1,
            },
            "evaluations": [
                {
                    "case_id": "bad-1",
                    "case_hash": "a" * 64,
                    "expected_defect": True,
                    "reported_defect": True,
                    "human_agrees": True,
                    "independent_contribution": True,
                    "labelled_by": "human-evaluator",
                    "label_evidence": "Reproduced and labelled as a defect",
                },
                {
                    "case_id": "good-1",
                    "case_hash": "b" * 64,
                    "expected_defect": False,
                    "reported_defect": False,
                    "human_agrees": True,
                    "independent_contribution": False,
                    "labelled_by": "human-evaluator",
                    "label_evidence": "Reviewed and labelled as a valid delivery",
                },
            ],
        }
    )


class BadCaseRegistryTests(unittest.TestCase):
    def setUp(self):
        self.engine = GovernanceEngine()
        self.contract = self.engine.compile_contract(ready_source())

    def test_confirmed_case_requires_human_confirmation_evidence(self):
        with self.assertRaisesRegex(ValueError, "confirmed_by"):
            compile_bad_case_registry(
                {
                    "registry_id": "bad",
                    "cases": [
                        {
                            "id": "case",
                            "title": "Case",
                            "category": "security",
                            "counterexample": "break it",
                            "expected": "reject",
                            "status": "confirmed",
                            "source": "test",
                        }
                    ],
                }
            )

    def test_hidden_cases_reach_inspectors_and_verifier_but_not_owner(self):
        registry = confirmed_registry()
        plan = self.engine.route(self.contract, registry)
        handoff = self.engine.delivery_handoff(self.contract, plan)

        self.assertEqual(plan["hidden_case_ids"], ["audit-replay"])
        self.assertNotIn("audit-replay", str(handoff["owner_task"]))
        self.assertIn("audit-replay", str(handoff["inspector_tasks"]))
        self.assertIn("audit-replay", str(handoff["final_verifier_task"]))

    def test_confirmed_registry_case_cannot_be_revealed_to_owner(self):
        with self.assertRaisesRegex(ValueError, "hidden_from_owner"):
            compile_bad_case_registry(
                {
                    "registry_id": "leaky",
                    "cases": [
                        {
                            "id": "leaky-case",
                            "title": "Leaky case",
                            "category": "security",
                            "counterexample": "Known hidden exploit",
                            "expected": "Reject",
                            "status": "confirmed",
                            "source": "human review",
                            "confirmed_by": "human-evaluator",
                            "confirmation_evidence": "Reproduced",
                            "hidden_from_owner": False,
                        }
                    ],
                }
            )


class InspectorCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = GovernanceEngine()
        self.contract = self.engine.compile_contract(ready_source())
        self.plan = self.engine.route(self.contract)

    def _payload(self, profile):
        return {
            "contract": self.contract,
            "plan": self.plan,
            "repair_round": 0,
            "deterministic_results": [
                {
                    "name": "unit",
                    "command": "python3 -m unittest",
                    "status": "passed",
                    "required": True,
                    "evidence": "all passed",
                }
            ],
            "findings": [
                {
                    "id": "security-1",
                    "lane": "security",
                    "title": "审计包可伪造",
                    "severity": "high",
                    "confidence": 0.99,
                    "evidence": "replay accepted",
                    "reproduction": "tamper, rehash, replay",
                    "introduced_by_change": True,
                }
            ],
            "inspector_calibrations": [profile],
        }

    def test_uncalibrated_profile_stays_shadow_and_cannot_block(self):
        profile = compile_inspector_calibration(
            {
                "lane_id": "security",
                "evaluations": [
                    {
                        "case_id": "one",
                        "case_hash": "c" * 64,
                        "expected_defect": True,
                        "reported_defect": True,
                        "human_agrees": True,
                        "independent_contribution": True,
                        "labelled_by": "human-evaluator",
                        "label_evidence": "Confirmed defect",
                    }
                ],
            }
        )
        verdict = self.engine.adjudicate(self._payload(profile))

        self.assertEqual(profile["mode"], "shadow")
        self.assertEqual(verdict["decision"], "ready_for_final_verification")
        self.assertEqual(len(verdict["shadow_findings"]), 1)
        self.assertFalse(verdict["semantic_blockers"])

    def test_shadow_finding_cannot_stop_flow_by_claiming_dispute_or_human_need(self):
        profile = compile_inspector_calibration(
            {
                "lane_id": "security",
                "evaluations": [
                    {
                        "case_id": "one",
                        "case_hash": "d" * 64,
                        "expected_defect": True,
                        "reported_defect": True,
                        "human_agrees": True,
                        "independent_contribution": True,
                        "labelled_by": "human-evaluator",
                        "label_evidence": "Confirmed defect",
                    }
                ],
            }
        )
        payload = self._payload(profile)
        payload["findings"][0]["disputed"] = True
        payload["findings"][0]["requires_human"] = True

        verdict = self.engine.adjudicate(payload)

        self.assertEqual(verdict["decision"], "ready_for_final_verification")
        self.assertEqual(len(verdict["shadow_findings"]), 1)

    def test_calibrated_profile_can_contribute_a_blocker(self):
        profile = blocking_profile()
        verdict = self.engine.adjudicate(self._payload(profile))

        self.assertEqual(profile["mode"], "blocking")
        self.assertEqual(verdict["decision"], "repair_once")
        self.assertEqual(len(verdict["semantic_blockers"]), 1)


if __name__ == "__main__":
    unittest.main()
