from __future__ import annotations

import unittest

from orchestrator.quality import (
    aggregate_findings,
    build_verification_plan,
    compare_contracts,
    decide_release_readiness,
    effective_risk,
)


class QualityPolicyTests(unittest.TestCase):
    def test_sensitive_surface_raises_risk(self):
        self.assertEqual(
            effective_risk({"risk": "low", "change_surfaces": ["auth"]}),
            "high",
        )

    def test_program_gates_are_not_acceptance_checks(self):
        plan = build_verification_plan(
            {
                "contract": {
                    "risk": "medium",
                    "change_surfaces": ["api"],
                }
            }
        )
        self.assertTrue(plan["program_gates"])
        self.assertTrue(
            all(not gate["acceptance_check"] for gate in plan["program_gates"])
        )
        self.assertTrue(all(lane["read_only"] for lane in plan["independent_lanes"]))

    def test_high_risk_plan_is_dimension_specific(self):
        plan = build_verification_plan(
            {
                "contract": {
                    "risk": "high",
                    "change_surfaces": ["auth", "ui", "dependency"],
                    "security_sensitive": True,
                    "subjective": True,
                }
            }
        )
        lane_ids = {lane["id"] for lane in plan["independent_lanes"]}
        self.assertIn("security_falsification", lane_ids)
        self.assertIn("experience_falsification", lane_ids)
        self.assertIn("test_oracle_falsification", lane_ids)

    def test_findings_filter_noise_and_batch_one_repair(self):
        result = aggregate_findings(
            {
                "required_lanes": ["security"],
                "completed_lanes": ["security"],
                "program_gates": [{"id": "tests", "status": "passed"}],
                "findings": [
                    {
                        "dimension": "security",
                        "severity": "high",
                        "confidence": 92,
                        "summary": "Untrusted redirect reaches the callback",
                        "location": "auth.py:10",
                        "reproducible": True,
                        "source": "security lane",
                    },
                    {
                        "dimension": "architecture",
                        "severity": "medium",
                        "confidence": 40,
                        "summary": "Maybe split this function",
                    },
                ],
            }
        )
        self.assertEqual(result["decision"], "repair_once")
        self.assertEqual(len(result["blocking_findings"]), 1)
        self.assertEqual(len(result["filtered_findings"]), 1)

    def test_second_failed_round_escalates(self):
        result = aggregate_findings(
            {
                "repair_rounds_used": 1,
                "program_gates": [{"id": "tests", "status": "failed"}],
                "findings": [],
            }
        )
        self.assertEqual(result["decision"], "escalate")

    def test_contract_drift_requires_human_decision(self):
        result = compare_contracts(
            {
                "baseline": {"goal": "A", "acceptance_criteria": ["X"]},
                "candidate": {"goal": "A", "acceptance_criteria": ["Y"]},
            }
        )
        self.assertTrue(result["drifted"])
        self.assertEqual(result["changes"][0]["field"], "acceptance_criteria")

    def test_release_readiness_preserves_human_boundary(self):
        result = decide_release_readiness(
            {
                "contract": {
                    "status": "ready",
                    "risk": "high",
                    "subjective": True,
                    "observability_signals": ["error rate"],
                },
                "verification": {"decision": "pass"},
            }
        )
        self.assertEqual(result["status"], "needs_human_decision")
        self.assertFalse(result["external_action_performed"])


if __name__ == "__main__":
    unittest.main()
