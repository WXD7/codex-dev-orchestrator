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
    advance_run_ledger,
    attest_contract_resolution,
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
from tests.test_governance import ready_source


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class GovernanceRuntimeV2Tests(unittest.TestCase):
    def setUp(self):
        self.engine = GovernanceEngine()
        self.contract = self.engine.compile_contract(ready_source())
        self.plan = self.engine.route(self.contract)
        self.token = generate_control_token()

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

    def test_complete_signed_ledger_stops_at_human_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            capsule = build_environment_capsule(repo, self.contract)
            ledger = create_run_ledger(self.contract, self.plan, self.token, "run_v2")
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
            ledger = create_run_ledger(self.contract, self.plan, self.token, "run_tamper")
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
        ledger = create_run_ledger(self.contract, self.plan, self.token)
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
        ledger = create_run_ledger(self.contract, self.plan, self.token)
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
            ledger = create_run_ledger(self.contract, self.plan, self.token)
            with AtomicLedgerStore(path) as store:
                store.save(ledger)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with AtomicLedgerStore(path) as store:
                loaded = store.load()
            self.assertEqual(loaded["run_id"], ledger["run_id"])

    def test_signed_agent_progress_updates_operator_snapshot_without_advancing_stage(self):
        ledger = create_run_ledger(self.contract, self.plan, self.token, "progress")
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
        ledger = create_run_ledger(self.contract, self.plan, self.token, "progress-tamper")
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
        ledger = create_run_ledger(
            resolved,
            resolved_plan,
            self.token,
            "resolved-run",
            attestation,
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


if __name__ == "__main__":
    unittest.main()
