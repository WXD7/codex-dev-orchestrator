from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.governance import GovernanceEngine
from orchestrator.governance_runtime import (
    AtomicLedgerStore,
    activate_delivery_handoff,
    advance_run_ledger,
    attest_contract_resolution,
    attest_intent_alignment,
    attest_race_selection,
    build_human_review_packet,
    build_environment_capsule,
    build_operator_snapshot,
    create_run_ledger,
    generate_control_token,
    record_agent_progress,
    replay_run_ledger,
    stop_run_ledger,
)
from tests.helpers import make_git_repo
from tests.test_governance import intent_alignment_for, ready_source


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class GovernanceRuntimeV2Tests(unittest.TestCase):
    def setUp(self):
        self.engine = GovernanceEngine()
        self.contract = self.engine.compile_contract(ready_source())
        self.plan = self.engine.route(self.contract)
        self.token = generate_control_token()
        self.intent_attestation = attest_intent_alignment(
            self.contract,
            self.plan,
            {
                "attested_by": "test-product-owner",
                "authority": "human",
                "evidence": "The displayed intent brief and independent inspection were confirmed",
                "approved_intent_hash": self.contract["intent_alignment"]["intent_hash"],
                "approved_inspection_hash": self.contract["intent_alignment"][
                    "inspection_hash"
                ],
                "approved_research_hash": self.contract["intent_alignment"][
                    "research_hash"
                ],
                "approved_technology_strategy_hash": self.contract[
                    "intent_alignment"
                ]["technology_strategy_hash"],
            },
            self.token,
            "test-task",
        )

    def _ledger(self, run_id=""):
        return create_run_ledger(
            self.contract,
            self.plan,
            self.token,
            run_id,
            intent_attestation=self.intent_attestation,
        )

    def _results(self):
        return [
            {
                "name": "python3 -m unittest",
                "command": "python3 -m unittest",
                "status": "passed",
                "required": True,
                "evidence": "149 tests passed",
            }
        ]

    def _inspection_packets(self):
        return [
            {
                "lane_id": lane["id"],
                "fresh_context": True,
                "read_only": True,
                "developer_transcript_visible": False,
                "peer_findings_visible": False,
                "evidence_bundle_sha256": "a" * 64,
            }
            for lane in self.plan["lanes"]
            if lane["kind"] != "deterministic"
        ]

    def test_owner_creation_requires_hmac_intent_attestation(self):
        with self.assertRaisesRegex(ValueError, "intent alignment attestation"):
            create_run_ledger(self.contract, self.plan, self.token, "missing-intent")

        ledger = self._ledger("confirmed-intent")
        self.assertEqual(
            ledger["intent_attestation"]["intent_hash"],
            self.contract["intent_alignment"]["intent_hash"],
        )
        activated = activate_delivery_handoff(
            self.contract, self.plan, ledger, self.token
        )
        self.assertEqual(activated["status"], "ready_for_control_plane")
        self.assertTrue(activated["owner_task"]["creation_allowed"])
        self.assertEqual(
            activated["intent_gate"]["tasks"][0]["progress_summary"],
            "人类意图签署已由可信控制器验签",
        )

    def test_intent_attestation_is_bound_to_contract_plan_and_external_task(self):
        forged_task = copy.deepcopy(self.intent_attestation)
        forged_task["external_task_ref"] = "different-task"
        with self.assertRaisesRegex(ValueError, "hash"):
            create_run_ledger(
                self.contract,
                self.plan,
                self.token,
                "forged-task",
                intent_attestation=forged_task,
            )

        changed_contract = self.engine.compile_contract(
            ready_source(goal="A different confirmed goal")
        )
        changed_plan = self.engine.route(changed_contract)
        with self.assertRaisesRegex(ValueError, "does not belong"):
            create_run_ledger(
                changed_contract,
                changed_plan,
                self.token,
                "forged-contract",
                intent_attestation=self.intent_attestation,
            )

        post_hash_tamper = copy.deepcopy(self.contract)
        post_hash_tamper["goal"] = "Changed without compiling a new contract hash"
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            create_run_ledger(
                post_hash_tamper,
                self.plan,
                self.token,
                "post-hash-tamper",
                intent_attestation=self.intent_attestation,
            )

    def test_complete_signed_ledger_stops_at_human_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            capsule = build_environment_capsule(repo, self.contract)
            ledger = self._ledger("run_v2")
            ledger = advance_run_ledger(
                ledger,
                self.plan,
                "environment_preflight",
                {"environment_capsule": capsule},
                self.token,
            )
            ledger = advance_run_ledger(
                ledger,
                self.plan,
                "owner_implementation",
                {
                    "owner_report": {
                        "owner_context_id": "owner-1",
                        "diff_sha256": "b" * 64,
                        "external_actions_performed": [],
                    }
                },
                self.token,
            )
            ledger = advance_run_ledger(
                ledger,
                self.plan,
                "deterministic_ci",
                {"deterministic_results": self._results()},
                self.token,
            )
            ledger = advance_run_ledger(
                ledger,
                self.plan,
                "independent_inspection",
                {"inspection_packets": self._inspection_packets()},
                self.token,
            )
            ledger = advance_run_ledger(
                ledger,
                self.plan,
                "adjudication",
                {
                    "adjudication": {
                        "contract_hash": self.contract["contract_hash"],
                        "plan_hash": self.plan["plan_hash"],
                        "decision": "ready_for_final_verification",
                    }
                },
                self.token,
            )
            ledger = advance_run_ledger(
                ledger,
                self.plan,
                "single_consolidated_repair",
                {"skipped": True},
                self.token,
            )
            ledger = advance_run_ledger(
                ledger,
                self.plan,
                "full_reverification",
                {"deterministic_results": self._results(), "full_plan_rerun": True},
                self.token,
            )
            ledger = advance_run_ledger(
                ledger,
                self.plan,
                "blind_final_verification",
                {
                    "final_verifier_report": {
                        "status": "passed",
                        "fresh_context": True,
                        "read_only": True,
                        "blind_to_owner_transcript": True,
                        "blind_to_prior_findings": True,
                        "must_kill_results": [],
                    }
                },
                self.token,
            )
            ledger = advance_run_ledger(
                ledger,
                self.plan,
                "human_handoff",
                {
                    "human_handoff": {
                        "status": "awaiting_human_decision",
                        "automated_approval": False,
                    }
                },
                self.token,
            )

        replay = replay_run_ledger(ledger, self.plan, self.token)
        self.assertTrue(replay["valid"])
        self.assertIsNone(replay["next_stage"])
        self.assertEqual(replay["status"], "awaiting_human_decision")
        packet = build_human_review_packet(ledger, self.plan, self.token)
        self.assertEqual(
            packet["human_attention"]["decision_requested"],
            "accept_or_request_changes",
        )
        self.assertEqual(
            packet["adjudication_summary"]["decision"],
            "ready_for_final_verification",
        )

    def test_recomputed_content_hash_cannot_forge_controller_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            ledger = self._ledger("run_tamper")
            ledger = advance_run_ledger(
                ledger,
                self.plan,
                "environment_preflight",
                {"environment_capsule": build_environment_capsule(repo, self.contract)},
                self.token,
            )
            ledger = advance_run_ledger(
                ledger,
                self.plan,
                "owner_implementation",
                {
                    "owner_report": {
                        "owner_context_id": "owner-1",
                        "diff_sha256": "b" * 64,
                        "external_actions_performed": [],
                    }
                },
                self.token,
            )
        forged = copy.deepcopy(ledger)
        artifacts = forged["stages"]["owner_implementation"]["artifacts"]
        artifacts["owner_report"]["diff_sha256"] = "c" * 64
        new_artifact_hash = digest(artifacts)
        forged["stages"]["owner_implementation"]["artifact_sha256"] = new_artifact_hash
        event = forged["events"][-1]
        event["artifact_sha256"] = new_artifact_hash
        unsigned = {key: value for key, value in event.items() if key not in {"event_hash", "controller_signature"}}
        event["event_hash"] = digest(unsigned)

        with self.assertRaisesRegex(ValueError, "signature"):
            replay_run_ledger(forged, self.plan, self.token)

    def test_stage_exit_without_required_artifact_cannot_advance(self):
        ledger = self._ledger()
        with self.assertRaisesRegex(ValueError, "environment_capsule"):
            advance_run_ledger(
                ledger,
                self.plan,
                "environment_preflight",
                {"agent_claim": "done", "exit_code": 0},
                self.token,
            )

    def test_preflight_detects_wrong_diff_root(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            nested = repo / "nested"
            nested.mkdir()
            capsule = build_environment_capsule(nested, self.contract)
        self.assertEqual(capsule["status"], "blocked")
        self.assertIn("assigned_repo_is_not_real_diff_root", capsule["blockers"])

    def test_preflight_preserves_first_git_porcelain_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            (repo / "README.md").write_text("# Changed\n", encoding="utf-8")
            capsule = build_environment_capsule(repo, self.contract)
        self.assertIn("README.md", capsule["git"]["changed_files"])

    def test_honest_stop_is_terminal_and_replayable(self):
        ledger = self._ledger()
        stopped = stop_run_ledger(
            ledger,
            self.plan,
            "required_evidence_unavailable",
            "Mutation runner is not installed",
            self.token,
        )
        self.assertEqual(replay_run_ledger(stopped, self.plan, self.token)["status"], "needs_human_decision")
        with self.assertRaisesRegex(ValueError, "stopped"):
            advance_run_ledger(stopped, self.plan, "environment_preflight", {}, self.token)

    def test_atomic_store_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.json"
            ledger = self._ledger()
            with AtomicLedgerStore(path) as store:
                store.save(ledger)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with AtomicLedgerStore(path) as store:
                loaded = store.load()
            self.assertEqual(loaded["run_id"], ledger["run_id"])

    def test_signed_agent_progress_updates_operator_snapshot_without_advancing_stage(self):
        ledger = self._ledger("progress")
        ledger = record_agent_progress(
            ledger,
            self.plan,
            {
                "agent_id": "owner",
                "display_name": "主实现者",
                "mission": "核心实现与统一修复",
                "stage": "environment_preflight",
                "enforcement_mode": "not_applicable",
                "execution_state": "waiting_on_dependency",
                "progress_summary": "任务已创建，等待环境预检",
                "current_difficulty": "尚未获得可写环境 capsule",
                "dependency": "environment_preflight",
                "needs_human": False,
                "source": {
                    "platform": "Kandev",
                    "task_id": "task-owner",
                    "session_id": "session-owner",
                },
            },
            self.token,
        )

        snapshot = build_operator_snapshot(ledger, self.plan, self.token)
        self.assertEqual(snapshot["execution"]["current_stage"], "environment_preflight")
        self.assertEqual(snapshot["execution"]["unique_agents"], 1)
        self.assertEqual(snapshot["delivery_verdict"], "unreviewed")
        self.assertEqual(snapshot["agents"][0]["current_difficulty"], "尚未获得可写环境 capsule")
        self.assertEqual(snapshot["agents"][0]["enforcement_mode"], "not_applicable")

        packet = build_human_review_packet(ledger, self.plan, self.token)
        self.assertEqual(packet["execution"]["unique_agents"], 1)
        self.assertFalse(packet["human_attention"]["automated_approval"])

    def test_unsigned_operator_snapshot_tampering_is_rejected(self):
        ledger = self._ledger("progress-tamper")
        ledger = record_agent_progress(
            ledger,
            self.plan,
            {
                "agent_id": "owner",
                "display_name": "主实现者",
                "mission": "核心实现",
                "stage": "owner_implementation",
                "execution_state": "working",
                "progress_summary": "正在实现",
                "current_difficulty": "无",
            },
            self.token,
        )
        forged = copy.deepcopy(ledger)
        forged["agents"]["owner"]["progress_summary"] = "伪造完成"

        with self.assertRaisesRegex(ValueError, "snapshot"):
            replay_run_ledger(forged, self.plan, self.token)

    def test_human_resolution_is_signed_and_bound_to_new_run_and_same_task(self):
        parent = self.engine.compile_contract(
            ready_source(
                uncertainties=[
                    {
                        "decision_id": "retention-policy",
                        "category": "policy_choice",
                        "statement": "Choose the retention period",
                        "state": "unknown",
                        "impact": "high",
                        "reversible": False,
                    }
                ]
            )
        )
        proposal = self.engine.propose_contract_resolution(
            parent,
            [
                {
                    "decision_id": "retention-policy",
                    "answer": "Retain for 30 days",
                    "answered_by": "product-owner",
                    "authority": "human",
                    "evidence": "Recorded in the human intake surface",
                }
            ],
        )
        resolved = proposal["proposed_contract"]
        resolved_plan = self.engine.route(resolved)
        attestation = attest_contract_resolution(
            proposal,
            parent,
            resolved_plan,
            {
                "attested_by": "product-owner",
                "authority": "human",
                "evidence": "Approved the displayed delta",
                "approved_delta_hash": proposal["delta_hash"],
            },
            self.token,
            "kandev-task-42",
        )
        intent_attestation = attest_intent_alignment(
            resolved,
            resolved_plan,
            {
                "attested_by": "product-owner",
                "authority": "human",
                "evidence": "Approved the displayed intent after resolving the delta",
                "approved_intent_hash": resolved["intent_alignment"]["intent_hash"],
                "approved_inspection_hash": resolved["intent_alignment"][
                    "inspection_hash"
                ],
                "approved_research_hash": resolved["intent_alignment"][
                    "research_hash"
                ],
                "approved_technology_strategy_hash": resolved[
                    "intent_alignment"
                ]["technology_strategy_hash"],
            },
            self.token,
            "kandev-task-42",
        )
        ledger = create_run_ledger(
            resolved,
            resolved_plan,
            self.token,
            "resolved-run",
            attestation,
            intent_attestation,
        )

        replay = replay_run_ledger(ledger, resolved_plan, self.token)
        packet = build_human_review_packet(ledger, resolved_plan, self.token)
        self.assertTrue(replay["valid"])
        self.assertTrue(packet["resolution"]["attested"])
        self.assertEqual(packet["resolution"]["external_task_ref"], "kandev-task-42")
        self.assertEqual(
            ledger["resolution_attestation"]["proposed_plan_hash"],
            resolved_plan["plan_hash"],
        )

        forged = copy.deepcopy(attestation)
        forged["external_task_ref"] = "another-task"
        with self.assertRaisesRegex(ValueError, "hash"):
            create_run_ledger(
                resolved,
                resolved_plan,
                self.token,
                "forged-resolution",
                forged,
                intent_attestation,
            )

    def test_owner_cannot_start_from_unresolved_contract(self):
        parent = self.engine.compile_contract(
            ready_source(
                uncertainties=[
                    {
                        "decision_id": "policy",
                        "category": "policy_choice",
                        "statement": "Choose a policy",
                        "state": "blocked",
                        "impact": "high",
                    }
                ]
            )
        )
        parent_plan = self.engine.route(parent)

        with self.assertRaisesRegex(ValueError, "must be ready"):
            create_run_ledger(parent, parent_plan, self.token, "too-early")


class BoundedTechnologyRaceTests(unittest.TestCase):
    def setUp(self):
        self.engine = GovernanceEngine()
        source = ready_source()
        source.pop("intent_alignment", None)
        source["intent_alignment"] = intent_alignment_for(source, bounded_race=True)
        self.contract = self.engine.compile_contract(source)
        self.plan = self.engine.route(self.contract)
        self.token = generate_control_token()
        alignment = self.contract["intent_alignment"]
        intent = attest_intent_alignment(
            self.contract,
            self.plan,
            {
                "attested_by": "race-product-owner",
                "authority": "human",
                "evidence": "The two paths, common tests, dimensions, budgets, stop rules, and fusion permission were confirmed",
                "approved_intent_hash": alignment["intent_hash"],
                "approved_inspection_hash": alignment["inspection_hash"],
                "approved_research_hash": alignment["research_hash"],
                "approved_technology_strategy_hash": alignment[
                    "technology_strategy_hash"
                ],
            },
            self.token,
            "race-task",
        )
        self.ledger = create_run_ledger(
            self.contract,
            self.plan,
            self.token,
            "bounded-race",
            intent_attestation=intent,
        )
        self.ledger = advance_run_ledger(
            self.ledger,
            self.plan,
            "environment_preflight",
            {
                "environment_capsule": {
                    "status": "ready",
                    "blockers": [],
                    "contract_hash": self.contract["contract_hash"],
                    "repository_root": "/tmp/race-repo",
                    "diff_root": "/tmp/race-repo",
                }
            },
            self.token,
        )

    def race_reports(self):
        race = self.plan["technology_race"]
        return [
            {
                "path_id": path_id,
                "context_id": "context-%s" % path_id,
                "worktree": "/tmp/worktree-%s" % path_id,
                "contract_hash": self.contract["contract_hash"],
                "research_hash": race["research_hash"],
                "strategy_hash": race["strategy_hash"],
                "common_test_suite_hash": race["common_test_suite_hash"],
                "evaluation_dimensions_hash": race["evaluation_dimensions_hash"],
                "frozen_data_sha256": "d" * 64,
                "diff_sha256": ("a" if index == 0 else "b") * 64,
                "common_test_results_sha256": ("c" if index == 0 else "e") * 64,
                "peer_candidate_visible": False,
                "transcript_shared": False,
                "external_actions_performed": [],
                "within_budget": True,
            }
            for index, path_id in enumerate(race["selected_path_ids"])
        ]

    def advance_candidates(self, reports=None):
        self.ledger = advance_run_ledger(
            self.ledger,
            self.plan,
            "parallel_technology_race",
            {"race_reports": reports or self.race_reports()},
            self.token,
        )

    def evaluation(self, transcript_visible=False):
        race = self.plan["technology_race"]
        reports = (
            self.ledger["stages"]["parallel_technology_race"]["artifacts"][
                "race_reports"
            ]
        )
        body = {
            "contract_hash": self.contract["contract_hash"],
            "plan_hash": self.plan["plan_hash"],
            "research_hash": race["research_hash"],
            "strategy_hash": race["strategy_hash"],
            "common_test_suite_hash": race["common_test_suite_hash"],
            "evaluation_dimensions_hash": race["evaluation_dimensions_hash"],
            "evaluator_declaration": {
                "context_id": "fresh-race-evaluator",
                "fresh_context": True,
                "read_only": True,
                "candidate_transcripts_visible": transcript_visible,
                "peer_findings_visible": False,
            },
            "candidate_results": [
                {
                    "path_id": report["path_id"],
                    "candidate_artifact_sha256": digest(report),
                    "common_test_suite_hash": race["common_test_suite_hash"],
                    "test_status": "passed",
                    "dimension_scores": {
                        "quality": 4.5 if index == 0 else 4.0,
                        "performance": 4.0,
                        "cost": 4.5 if index == 0 else 3.5,
                        "risk": 4.0,
                    },
                    "status": "qualified",
                    "evidence": "Frozen common tests and metrics passed for this candidate",
                }
                for index, report in enumerate(reports)
            ],
            "recommendation": {
                "decision": "keep_path",
                "selected_path_ids": ["path-a"],
                "explicit_benefits": [],
                "rationale": "Path A has the stronger quality/cost balance under the frozen rubric.",
            },
        }
        return {**body, "evaluation_hash": digest(body)}

    def advance_evaluation(self, evaluation=None):
        self.ledger = advance_run_ledger(
            self.ledger,
            self.plan,
            "race_evaluation",
            {"race_evaluation": evaluation or self.evaluation()},
            self.token,
        )

    def test_plan_and_handoff_show_two_named_isolated_race_agents(self):
        self.assertEqual(
            self.plan["sequence"][:4],
            [
                "environment_preflight",
                "parallel_technology_race",
                "race_evaluation",
                "race_human_selection",
            ],
        )
        handoff = self.engine.delivery_handoff(self.contract, self.plan)
        self.assertEqual(
            [item["display_name"] for item in handoff["technology_race"]["race_tasks"]],
            ["技术赛道一", "技术赛道二"],
        )
        self.assertEqual(
            handoff["technology_race"]["evaluator_task"]["display_name"],
            "统一赛马评测员",
        )
        self.assertFalse(handoff["owner_task"]["creation_allowed"])

    def test_race_paths_cannot_use_different_tests_or_shared_context(self):
        reports = self.race_reports()
        reports[1]["common_test_suite_hash"] = "f" * 64
        reports[1]["context_id"] = reports[0]["context_id"]
        with self.assertRaisesRegex(ValueError, "different tests"):
            self.advance_candidates(reports)

    def test_evaluator_is_fresh_read_only_and_cannot_see_lane_transcripts(self):
        self.advance_candidates()
        with self.assertRaisesRegex(ValueError, "candidate transcripts"):
            self.advance_evaluation(self.evaluation(transcript_visible=True))

    def test_human_keeps_winner_then_same_context_becomes_owner(self):
        self.advance_candidates()
        self.advance_evaluation()
        evaluation_hash = self.ledger["stages"]["race_evaluation"]["artifacts"][
            "race_evaluation"
        ]["evaluation_hash"]
        selection = attest_race_selection(
            self.ledger,
            self.plan,
            {
                "attested_by": "race-product-owner",
                "authority": "human",
                "evidence": "Reviewed the blinded common-test comparison",
                "approved_evaluation_hash": evaluation_hash,
                "decision": "keep_path",
                "selected_path_ids": ["path-a"],
                "selected_benefits": [],
                "owner_context_id": "context-path-a",
            },
            self.token,
            "race-task",
        )
        self.ledger = advance_run_ledger(
            self.ledger,
            self.plan,
            "race_human_selection",
            {"race_selection_attestation": selection},
            self.token,
        )
        activated = activate_delivery_handoff(
            self.contract, self.plan, self.ledger, self.token
        )
        self.assertTrue(activated["owner_task"]["creation_allowed"])
        self.assertEqual(activated["next_action"], "create_human_selected_owner")
        self.ledger = advance_run_ledger(
            self.ledger,
            self.plan,
            "owner_implementation",
            {
                "owner_report": {
                    "owner_context_id": "context-path-a",
                    "diff_sha256": "9" * 64,
                    "external_actions_performed": [],
                }
            },
            self.token,
        )
        self.assertEqual(
            self.ledger["stages"]["owner_implementation"]["status"], "completed"
        )

    def test_human_can_reject_all_and_owner_never_starts(self):
        self.advance_candidates()
        self.advance_evaluation()
        evaluation_hash = self.ledger["stages"]["race_evaluation"]["artifacts"][
            "race_evaluation"
        ]["evaluation_hash"]
        selection = attest_race_selection(
            self.ledger,
            self.plan,
            {
                "attested_by": "race-product-owner",
                "authority": "human",
                "evidence": "Neither candidate satisfies the real acceptance boundary",
                "approved_evaluation_hash": evaluation_hash,
                "decision": "reject_all",
                "selected_path_ids": [],
                "selected_benefits": [],
            },
            self.token,
            "race-task",
        )
        stopped = advance_run_ledger(
            self.ledger,
            self.plan,
            "race_human_selection",
            {"race_selection_attestation": selection},
            self.token,
        )
        self.assertEqual(stopped["status"], "needs_human_decision")
        with self.assertRaisesRegex(ValueError, "stopped"):
            advance_run_ledger(
                stopped,
                self.plan,
                "owner_implementation",
                {
                    "owner_report": {
                        "owner_context_id": "context-path-a",
                        "diff_sha256": "9" * 64,
                        "external_actions_performed": [],
                    }
                },
                self.token,
            )

    def test_pre_authorized_fusion_requires_a_new_integration_owner(self):
        self.advance_candidates()
        self.advance_evaluation()
        evaluation_hash = self.ledger["stages"]["race_evaluation"]["artifacts"][
            "race_evaluation"
        ]["evaluation_hash"]
        selection = attest_race_selection(
            self.ledger,
            self.plan,
            {
                "attested_by": "race-product-owner",
                "authority": "human",
                "evidence": "Use A's low integration cost and B's extension seam",
                "approved_evaluation_hash": evaluation_hash,
                "decision": "fuse_paths",
                "selected_path_ids": ["path-a", "path-b"],
                "selected_benefits": [
                    "Path A stateless adapter",
                    "Path B extension seam",
                ],
                "owner_context_id": "fusion-integration-owner",
                "integration_owner_context_id": "fusion-integration-owner",
            },
            self.token,
            "race-task",
        )
        self.ledger = advance_run_ledger(
            self.ledger,
            self.plan,
            "race_human_selection",
            {"race_selection_attestation": selection},
            self.token,
        )
        self.ledger = advance_run_ledger(
            self.ledger,
            self.plan,
            "owner_implementation",
            {
                "owner_report": {
                    "owner_context_id": "fusion-integration-owner",
                    "diff_sha256": "8" * 64,
                    "external_actions_performed": [],
                }
            },
            self.token,
        )
        self.assertEqual(
            self.ledger["stages"]["owner_implementation"]["status"], "completed"
        )


if __name__ == "__main__":
    unittest.main()
