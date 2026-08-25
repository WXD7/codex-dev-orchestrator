from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.delivery_bundle import scaffold_project
from orchestrator.governance import GovernanceEngine, integration_blueprint
from tests.helpers import make_git_repo


def ready_source(**overrides):
    value = {
        "title": "Add account settings",
        "goal": "Let a signed-in user update account settings",
        "users": ["Signed-in customer"],
        "outcomes": ["A saved setting is visible after reload"],
        "acceptance_criteria": ["Saving a valid setting persists it and shows confirmation"],
        "non_goals": ["No administrator bulk editing"],
        "constraints": ["Keep the public API compatible"],
        "forbidden_behaviors": ["Do not expose another tenant's settings"],
        "human_decisions": ["A human approves the final merge"],
        "deterministic_checks": ["python3 -m unittest"],
        "change_types": ["product_code", "ui"],
        "risk_flags": ["authorization", "multi_tenant", "user_experience"],
    }
    value.update(overrides)
    return value


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.engine = GovernanceEngine()

    def test_missing_oracle_and_boundaries_require_clarification(self):
        contract = self.engine.compile_contract({"goal": "Build it"})

        self.assertEqual(contract["status"], "needs_clarification")
        self.assertEqual(
            {item["id"] for item in contract["clarifications"]},
            {
                "target_users",
                "observable_outcome",
                "acceptance_criteria",
                "non_goals",
                "deterministic_evidence",
            },
        )
        for question in contract["question_gate"]["blocking_questions"]:
            self.assertEqual(question["decision_id"], question["id"])
            self.assertEqual(question["impact"], "high")
            self.assertIn("decision_owner", question)
            self.assertIn("reversible", question)
            self.assertIn("consequence", question)

    def test_contract_hash_is_stable_and_changes_with_intent(self):
        first = self.engine.compile_contract(ready_source())
        same = self.engine.compile_contract(ready_source())
        changed = self.engine.compile_contract(
            ready_source(acceptance_criteria=["A different observable result"])
        )

        self.assertEqual(first["status"], "ready")
        self.assertEqual(first["contract_hash"], same["contract_hash"])
        self.assertNotEqual(first["contract_hash"], changed["contract_hash"])

    def test_risk_inference_is_a_signal_and_explicit_unknown_flags_fail(self):
        source = ready_source(risk_flags=[], goal="Change OAuth login and billing migration")
        contract = self.engine.compile_contract(source)

        self.assertEqual(contract["risk"]["level"], "high")
        self.assertIn("authentication", contract["risk"]["inferred_flags"])
        self.assertIn("billing", contract["risk"]["inferred_flags"])

        with self.assertRaisesRegex(ValueError, "unknown risk_flags"):
            self.engine.compile_contract(ready_source(risk_flags=["made_up_risk"]))

    def test_negative_guardrails_do_not_expand_inferred_risk(self):
        contract = self.engine.compile_contract(
            ready_source(
                goal="Build a legal billing review UI with a stable public API",
                outcomes=["A reviewer can inspect calculated billing entries"],
                acceptance_criteria=["The public API returns the documented billing result"],
                constraints=["Do not add a model API key or expose credentials"],
                forbidden_behaviors=["Do not deploy or release to production"],
                change_types=["product_code", "ui"],
                risk_flags=["billing", "privacy", "public_api", "user_experience"],
            )
        )

        self.assertEqual(contract["risk"]["level"], "high")
        self.assertEqual(
            contract["risk"]["flags"],
            ["billing", "privacy", "public_api", "user_experience"],
        )
        self.assertNotIn("production_release", contract["risk"]["inferred_flags"])
        self.assertNotIn("secrets", contract["risk"]["inferred_flags"])

    def test_compiled_contract_rejects_post_hash_intent_or_risk_changes(self):
        contract = self.engine.compile_contract(ready_source())
        changed_intent = dict(contract, goal="Silently changed goal")
        changed_risk = dict(contract, risk={**contract["risk"], "level": "low"})

        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            self.engine.route(changed_intent)
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            self.engine.route(changed_risk)

    def test_question_gate_interrupts_only_for_policy_and_domain_uncertainty(self):
        contract = self.engine.compile_contract(
            ready_source(
                uncertainties=[
                    {
                        "id": "policy",
                        "category": "policy_choice",
                        "statement": "Choose whether legacy clients remain supported",
                        "question": "Must legacy clients remain supported?",
                        "status": "unresolved",
                    },
                    {
                        "id": "invariant",
                        "category": "engineering_invariant",
                        "statement": "The real Git diff root must match the assigned root",
                        "status": "unresolved",
                    },
                    {
                        "id": "research",
                        "category": "researchable_fact",
                        "statement": "The installed Kandev profile shape must be discovered",
                        "status": "unresolved",
                    },
                ]
            )
        )

        self.assertEqual(contract["status"], "needs_clarification")
        self.assertEqual([item["id"] for item in contract["clarifications"]], ["policy"])
        routes = {
            item["id"]: item["route"]
            for item in contract["question_gate"]["non_blocking_routes"]
        }
        self.assertEqual(routes["invariant"], "prove_from_repository")
        self.assertEqual(routes["research"], "research_without_interrupting_owner")

    def test_question_ledger_binds_impact_acceptance_and_resolution_delta(self):
        contract = self.engine.compile_contract(
            ready_source(
                uncertainties=[
                    {
                        "decision_id": "activity-cardinality",
                        "category": "policy_choice",
                        "statement": "程序活动是单值还是集合",
                        "question": "允许多个程序活动并存吗？",
                        "state": "contested",
                        "impact": "high",
                        "acceptance_ids": ["typed-facts"],
                        "consequence": "不同答案会改变 Schema 和 UI",
                        "proposed_default": "使用集合",
                        "decision_owner": "human",
                        "reversible": False,
                    }
                ]
            )
        )

        self.assertEqual(contract["status"], "needs_clarification")
        question = contract["question_gate"]["blocking_questions"][0]
        self.assertEqual(question["decision_id"], "activity-cardinality")
        self.assertEqual(question["acceptance_ids"], ["typed-facts"])

        proposal = self.engine.propose_contract_resolution(
            contract,
            [
                {
                    "decision_id": "activity-cardinality",
                    "answer": "允许多个程序活动并存",
                    "answered_by": "product-owner",
                    "authority": "human",
                    "evidence": "用户在需求闸门中确认",
                }
            ],
        )

        self.assertEqual(proposal["status"], "awaiting_human_attestation")
        self.assertFalse(proposal["resume"]["allowed_now"])
        self.assertEqual(proposal["proposed_contract"]["status"], "ready")
        self.assertNotEqual(
            proposal["delta"]["parent_contract_hash"],
            proposal["delta"]["proposed_contract_hash"],
        )

    def test_low_impact_reversible_assumption_is_visible_but_nonblocking(self):
        contract = self.engine.compile_contract(
            ready_source(
                uncertainties=[
                    {
                        "decision_id": "label-copy",
                        "category": "policy_choice",
                        "statement": "按钮文案使用哪个同义词",
                        "state": "assumed",
                        "impact": "low",
                        "proposed_default": "提交",
                        "decision_owner": "human",
                        "reversible": True,
                    }
                ]
            )
        )

        self.assertEqual(contract["status"], "ready")
        self.assertEqual(
            contract["question_gate"]["non_blocking_routes"][0]["decision_id"],
            "label-copy",
        )

    def test_delegated_high_impact_decision_remains_blocking(self):
        contract = self.engine.compile_contract(
            ready_source(
                uncertainties=[
                    {
                        "decision_id": "delegated-policy",
                        "category": "policy_choice",
                        "statement": "Human owner must choose the policy",
                        "state": "delegated",
                        "impact": "high",
                        "decision_owner": "human",
                    }
                ]
            )
        )

        self.assertEqual(contract["status"], "needs_clarification")
        self.assertEqual(
            contract["question_gate"]["blocking_questions"][0]["decision_id"],
            "delegated-policy",
        )

    def test_empty_contract_resolution_is_rejected(self):
        contract = self.engine.compile_contract(ready_source())

        with self.assertRaisesRegex(ValueError, "must change"):
            self.engine.propose_contract_resolution(contract, [])


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.engine = GovernanceEngine()

    def test_low_risk_work_stays_single_owner_with_deterministic_ci(self):
        contract = self.engine.compile_contract(
            ready_source(
                goal="Correct a documentation typo",
                change_types=["documentation"],
                risk_flags=[],
                constraints=[],
                forbidden_behaviors=[],
            )
        )
        plan = self.engine.route(contract)

        self.assertEqual(plan["risk_level"], "low")
        self.assertEqual([lane["id"] for lane in plan["lanes"]], ["deterministic-ci"])
        self.assertEqual(plan["execution"]["owner_context"], "continuous")
        self.assertEqual(plan["repair_policy"]["max_automatic_rounds"], 1)

    def test_high_risk_work_fans_out_by_failure_mode_not_job_title(self):
        contract = self.engine.compile_contract(ready_source())
        plan = self.engine.route(contract)
        lane_ids = {lane["id"] for lane in plan["lanes"]}

        self.assertTrue(
            {
                "deterministic-ci",
                "contract-domain-semantics",
                "state-trust-boundaries",
                "test-oracle-falsification",
                "security",
                "data-compatibility",
                "e2e-ux",
                "reliability-cost",
                "adversarial-falsification",
            }.issubset(lane_ids)
        )
        for lane in plan["lanes"]:
            self.assertFalse(lane["write_access"])
            self.assertFalse(lane["peer_findings_visible"])
        self.assertIn("high_risk_policy_and_release", plan["human_gates"])
        self.assertEqual(
            [
                "contract-domain-semantics",
                "state-trust-boundaries",
                "test-oracle-falsification",
            ],
            [lane["id"] for lane in plan["lanes"][1:4]],
        )
        self.assertEqual(plan["sequence"][-2:], ["blind_final_verification", "human_handoff"])
        self.assertEqual(plan["checkpoint_policy"]["agent_access_to_control_token"], False)

    def test_context_packets_are_minimal_read_only_and_hash_bound(self):
        contract = self.engine.compile_contract(ready_source())
        plan = self.engine.route(contract)
        contexts = self.engine.context_packets(contract, plan)

        self.assertTrue(contexts)
        self.assertNotIn("deterministic-ci", {item["lane_id"] for item in contexts})
        for item in contexts:
            self.assertEqual(item["permissions"]["repository"], "read")
            self.assertFalse(item["permissions"]["external_writes"])
            self.assertFalse(item["isolation"]["developer_transcript_visible"])
            self.assertEqual(item["contract_hash"], contract["contract_hash"])

        broken = dict(plan)
        broken["contract_hash"] = "different"
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.engine.context_packets(contract, broken)

        weakened = {**plan, "lanes": plan["lanes"][:-1]}
        with self.assertRaisesRegex(ValueError, "plan integrity check failed"):
            self.engine.context_packets(contract, weakened)


class AdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.engine = GovernanceEngine()
        self.contract = self.engine.compile_contract(ready_source())
        self.plan = self.engine.route(self.contract)

    def payload(self, **values):
        result = {
            "contract": self.contract,
            "plan": self.plan,
            "repair_round": 0,
            "deterministic_results": [
                {
                    "name": "unit",
                    "status": "passed",
                    "required": True,
                    "command": "python3 -m unittest",
                    "evidence": "42 tests passed",
                }
            ],
            "findings": [],
        }
        result.update(values)
        return result

    def test_high_signal_reproducible_finding_creates_one_repair_package(self):
        finding = {
            "id": "sec-1",
            "lane": "security",
            "title": "Tenant authorization is bypassed",
            "severity": "high",
            "confidence": 94,
            "location": "app/settings.py:42",
            "evidence": "The query filters only by record id",
            "reproduction": "Request tenant B record id while authenticated as tenant A",
            "introduced_by_change": True,
        }
        duplicate = dict(finding, id="req-2", lane="contract-domain-semantics", confidence=85)
        verdict = self.engine.adjudicate(self.payload(findings=[finding, duplicate]))

        self.assertEqual(verdict["decision"], "repair_once")
        self.assertEqual(len(verdict["semantic_blockers"]), 1)
        self.assertEqual(verdict["metrics"]["deduplicated_findings"], 1)
        self.assertEqual(verdict["repair_package"]["owner"], "original_owner_context")

    def test_low_confidence_preexisting_and_unreproduced_findings_do_not_block(self):
        findings = [
            {
                "lane": "code-architecture",
                "title": "Maybe complex",
                "severity": "high",
                "confidence": 60,
                "evidence": "subjective",
                "reproduction": "none",
                "introduced_by_change": True,
            },
            {
                "lane": "security",
                "title": "Old issue",
                "severity": "high",
                "confidence": 99,
                "evidence": "exists on main",
                "reproduction": "reproduces on base and head",
                "introduced_by_change": False,
            },
            {
                "lane": "security",
                "title": "No proof",
                "severity": "critical",
                "confidence": 99,
                "evidence": "dangerous-looking call",
                "reproduction": "",
                "introduced_by_change": True,
            },
        ]
        verdict = self.engine.adjudicate(self.payload(findings=findings))

        self.assertEqual(verdict["decision"], "ready_for_final_verification")
        self.assertEqual(len(verdict["rejected_findings"]), 3)

    def test_required_ci_failure_blocks_and_second_round_escalates(self):
        failed = [
            {
                "name": "unit",
                "status": "failed",
                "required": True,
                "command": "python3 -m unittest",
                "evidence": "one assertion failed",
            }
        ]
        first = self.engine.adjudicate(
            self.payload(deterministic_results=failed, repair_round=0)
        )
        second = self.engine.adjudicate(
            self.payload(deterministic_results=failed, repair_round=1)
        )

        self.assertEqual(first["decision"], "repair_once")
        self.assertEqual(second["decision"], "human_decision")
        self.assertEqual(second["repair_package"]["automatic_rounds_remaining"], 0)

    def test_missing_required_deterministic_evidence_blocks(self):
        verdict = self.engine.adjudicate(
            self.payload(deterministic_results=[], repair_round=0)
        )

        self.assertEqual(verdict["decision"], "repair_once")
        self.assertEqual(verdict["deterministic_blockers"][0]["status"], "missing")

    def test_unselected_lane_and_unproven_change_scope_cannot_block(self):
        findings = [
            {
                "lane": "invented-reviewer",
                "title": "Arbitrary blocker",
                "severity": "critical",
                "confidence": 100,
                "evidence": "unsupported lane",
                "reproduction": "run it",
                "introduced_by_change": True,
            },
            {
                "lane": "security",
                "title": "Scope is unknown",
                "severity": "high",
                "confidence": 100,
                "evidence": "could exist",
                "reproduction": "run it",
                "introduced_by_change": "unknown",
            },
        ]
        verdict = self.engine.adjudicate(self.payload(findings=findings))

        self.assertEqual(verdict["decision"], "ready_for_final_verification")
        self.assertEqual(
            {item["rejected_reason"] for item in verdict["rejected_findings"]},
            {"lane_not_enabled_by_plan", "introduced_by_change_unproven"},
        )

    def test_disputed_high_risk_fact_goes_directly_to_human(self):
        finding = {
            "lane": "data-compatibility",
            "title": "Migration reversibility is disputed",
            "severity": "high",
            "confidence": 90,
            "evidence": "Down migration conflicts with retained rows",
            "reproduction": "Run up then down against the fixture",
            "introduced_by_change": True,
            "disputed": True,
        }
        verdict = self.engine.adjudicate(self.payload(findings=[finding]))
        self.assertEqual(verdict["decision"], "human_decision")

    def test_cross_lane_root_cause_is_merged_into_one_repair_item(self):
        common = {
            "severity": "high",
            "confidence": 0.95,
            "introduced_by_change": True,
            "root_cause_key": "typed-correction-trust-boundary",
            "violated_invariant": "Corrections retain their declared type",
            "counterexample": "Submit integer 0 through the Boolean-only UI",
            "artifact_refs": ["tests/correction-e2e.json"],
            "reproduction": {
                "preconditions": ["Open correction form"],
                "steps": ["Submit integer 0"],
                "expected": "Integer 0 is stored",
                "actual": "The UI cannot submit it",
            },
            "evidence": "Browser trace and API response disagree",
        }
        findings = [
            dict(common, id="state-1", lane="state-trust-boundaries", title="Typed correction is lost"),
            dict(common, id="oracle-1", lane="test-oracle-falsification", title="E2E misses typed correction"),
        ]
        verdict = self.engine.adjudicate(
            self.payload(
                findings=findings,
                inspector_telemetry=[
                    {
                        "lane": "state-trust-boundaries",
                        "duration_ms": 1200,
                        "input_tokens": 300,
                        "output_tokens": 90,
                    }
                ],
            )
        )

        self.assertEqual(verdict["decision"], "repair_once")
        self.assertEqual(len(verdict["repair_package"]["root_causes"]), 1)
        self.assertEqual(
            set(verdict["repair_package"]["root_causes"][0]["contributing_lanes"]),
            {"state-trust-boundaries", "test-oracle-falsification"},
        )
        state_metrics = verdict["metrics"]["per_inspector"]["state-trust-boundaries"]
        self.assertEqual(state_metrics["duration_ms"], 1200)
        self.assertEqual(state_metrics["input_tokens"], 300)


class IntegrationAndScaffoldTests(unittest.TestCase):
    def test_blueprint_keeps_governance_stateless(self):
        blueprint = integration_blueprint()
        governance = next(
            item for item in blueprint["components"] if item["name"] == "AI Delivery Governance"
        )
        self.assertIn("tasks", governance["must_not_own"])
        self.assertEqual(blueprint["default_path"]["development_control_plane"], "Kandev")

    def test_scaffold_writes_a_versioned_bundle_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            result = scaffold_project(repo, ready_source())

            self.assertEqual(result["contract_status"], "ready")
            self.assertIn("delivery-handoff.json", result["next"])
            self.assertTrue((repo / ".ai-delivery" / "contract.json").is_file())
            self.assertTrue((repo / ".ai-delivery" / "verification-plan.json").is_file())
            self.assertTrue((repo / ".ai-delivery" / "delivery-handoff.json").is_file())
            registry = json.loads(
                (repo / ".ai-delivery" / "bad-case-registry.json").read_text(
                    encoding="utf-8"
                )
            )
            protocol = json.loads(
                (repo / ".ai-delivery" / "runtime-protocol.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(protocol["schema_version"], "2.1")
            self.assertEqual(
                protocol["bad_case_registry_hash"], registry["registry_hash"]
            )
            self.assertTrue(
                protocol["contract_resolution"]["human_delta_attestation_required"]
            )
            content = (repo / ".ai-delivery" / "CONSTITUTION.md").read_text(
                encoding="utf-8"
            )
            with self.assertRaisesRegex(FileExistsError, "no files were changed"):
                scaffold_project(repo, ready_source(goal="Different goal"))
            self.assertEqual(
                (repo / ".ai-delivery" / "CONSTITUTION.md").read_text(encoding="utf-8"),
                content,
            )

    def test_delivery_handoff_preserves_owner_context_and_inspector_boundaries(self):
        engine = GovernanceEngine()
        contract = engine.compile_contract(ready_source())
        plan = engine.route(contract)
        handoff = engine.delivery_handoff(contract, plan)

        self.assertEqual(handoff["status"], "ready_for_control_plane")
        self.assertEqual(handoff["owner_task"]["session"], "continuous_until_handoff_or_single_repair")
        self.assertFalse(handoff["executor"]["api_key_allowed"])
        self.assertTrue(handoff["inspector_tasks"])
        self.assertTrue(
            all(
                item["session"] == "new_task_and_fresh_session"
                for item in handoff["inspector_tasks"]
            )
        )
        self.assertIn("push", handoff["kandev"]["workflow_warning"])


if __name__ == "__main__":
    unittest.main()
