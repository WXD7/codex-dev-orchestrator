from __future__ import annotations

import io
import json
import unittest

from orchestrator.governance import compile_work_contract, route_context
from orchestrator.governance_mcp import GovernanceMCP


class GovernanceTests(unittest.TestCase):
    def test_low_risk_goal_keeps_one_owner_and_program_gate(self):
        contract = compile_work_contract(
            {
                "goal": "Add a read-only version endpoint",
                "risk": "low",
                "acceptance_criteria": ["GET /version returns the build version"],
                "change_surfaces": ["api"],
            }
        )
        self.assertEqual(contract["execution_policy"]["default_owner"], "one_continuous_context")
        self.assertEqual(contract["execution_policy"]["max_automatic_repair_rounds"], 1)
        self.assertFalse(contract["requires_immediate_human_decision"])
        self.assertEqual(contract["verification_gates"][0]["kind"], "program")
        self.assertIn("do not create project-manager", contract["task_instruction"].lower())
        self.assertEqual(contract["status"], "ready")
        self.assertEqual(len(contract["contract_hash"]), 64)

    def test_missing_outcome_evidence_pauses_for_clarification(self):
        contract = compile_work_contract({"goal": "Improve the product", "risk": "low"})
        self.assertEqual(contract["status"], "needs_clarification")
        self.assertEqual(contract["acceptance_criteria"], [])
        self.assertIn("CLARIFICATION REQUIRED", contract["task_instruction"])

    def test_ci_only_acceptance_is_rejected(self):
        contract = compile_work_contract(
            {
                "goal": "Add a user feature",
                "risk": "low",
                "acceptance_criteria": ["All tests pass"],
            }
        )
        self.assertEqual(contract["status"], "needs_clarification")

    def test_high_risk_and_subjective_goal_escalates(self):
        contract = compile_work_contract(
            {
                "goal": "Replace authentication and redesign onboarding",
                "risk": "medium",
                "security_sensitive": True,
                "subjective": True,
                "acceptance_criteria": ["A user can sign in and understand the onboarding"],
                "security_boundaries": ["Browser input to authenticated session"],
                "observability_signals": ["sign-in success and failure events"],
            }
        )
        self.assertEqual(contract["risk"], "high")
        self.assertTrue(contract["requires_immediate_human_decision"])
        self.assertTrue(any(item["kind"] == "agent" for item in contract["verification_gates"]))

    def test_route_continues_high_affinity_topic(self):
        result = route_context(
            {
                "work": "Fix auth token refresh in src/auth/session.py",
                "purpose": "repair",
                "project_id": "p1",
                "repo_path": "/repo",
                "touched_paths": ["src/auth/session.py"],
                "candidates": [
                    {
                        "topic_id": "topic-auth",
                        "codex_session_id": "session-auth",
                        "project_id": "p1",
                        "repo_path": "/repo",
                        "status": "paused",
                        "title": "Implement auth token refresh",
                        "summary": "Changed session.py refresh behavior",
                        "touched_paths": ["src/auth/session.py"],
                    }
                ],
            }
        )
        self.assertEqual(result["decision"], "continue_existing")
        self.assertEqual(result["topic_id"], "topic-auth")
        self.assertEqual(result["codex_session_id"], "session-auth")
        self.assertIn("execute-topic", result["orchestrator_cli_hint"])
        self.assertIn("--resume session-auth", result["orchestrator_cli_hint"])
        self.assertNotIn("lh task run", result["orchestrator_cli_hint"])

    def test_adversarial_review_always_gets_fresh_context(self):
        result = route_context(
            {"work": "Review auth", "purpose": "adversarial_review", "candidates": [{"id": "x"}]}
        )
        self.assertEqual(result["decision"], "new_context")

    def test_mcp_surface_has_only_policy_tools(self):
        server = GovernanceMCP()
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = sorted(item["name"] for item in response["result"]["tools"])
        self.assertEqual(
            names,
            [
                "aggregate_verification_findings",
                "build_verification_plan",
                "compare_engineering_contracts",
                "compile_engineering_goal",
                "decide_release_readiness",
                "get_codex_quota_advice",
                "route_to_context",
            ],
        )

    def test_mcp_stdio_round_trip(self):
        stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "compile_engineering_goal", "arguments": {
                    "goal": "Ship it",
                    "risk": "low",
                    "acceptance_criteria": ["The operator can observe the shipped result"]
                }
            }}) + "\n"
        )
        stdout = io.StringIO()
        GovernanceMCP().serve(stdin, stdout)
        payload = json.loads(stdout.getvalue())
        self.assertIn("one_continuous_context", payload["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
