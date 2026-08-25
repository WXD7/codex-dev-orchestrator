"""V2.1 runtime invariants for an external AI delivery control plane.

The governance MCP remains stateless.  This module supplies a narrow, caller-
owned checkpoint ledger for Kandev, Symphony, or another trusted controller.
It does not launch agents, own tasks, or perform external actions.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:  # pragma: no cover - exercised on the supported macOS/Linux runtime.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


LEDGER_VERSION = "2.1"
PASS_STATUSES = frozenset({"pass", "passed"})
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
STOP_REASONS = frozenset(
    {
        "ambiguity",
        "artifact_invariant_failed",
        "control_plane_failure",
        "external_or_irreversible_action",
        "high_risk_dispute",
        "repair_did_not_converge",
        "required_evidence_unavailable",
    }
)
AGENT_EXECUTION_STATES = frozenset(
    {
        "queued",
        "starting",
        "working",
        "waiting_on_dependency",
        "waiting_on_human",
        "finished",
        "failed",
        "stopped",
    }
)
INSPECTOR_ENFORCEMENT_MODES = frozenset(
    {"not_applicable", "shadow", "blocking"}
)
STAGE_PRESENTATION = {
    "environment_preflight": "环境预检",
    "owner_implementation": "主实现",
    "deterministic_ci": "确定性门禁",
    "independent_inspection": "独立检查",
    "adjudication": "统一裁决",
    "single_consolidated_repair": "单次统一修复",
    "full_reverification": "全量复验",
    "blind_final_verification": "最终盲验",
    "human_handoff": "人工交接",
}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_token(control_token: str) -> str:
    if not isinstance(control_token, str) or len(control_token) < 32:
        raise ValueError("control_token must be an unexposed random value of at least 32 characters")
    return control_token


def generate_control_token() -> str:
    """Return a local run token; the controller must never pass it to an agent."""
    return secrets.token_urlsafe(32)


def _resolution_attestation_body(
    attestation: Mapping[str, Any],
) -> Dict[str, Any]:
    if not isinstance(attestation, Mapping):
        raise ValueError("contract resolution attestation must be an object")
    return {
        key: copy.deepcopy(value)
        for key, value in attestation.items()
        if key not in {"attestation_hash", "controller_signature"}
    }


def validate_contract_resolution_attestation(
    attestation: Mapping[str, Any],
    contract: Mapping[str, Any],
    plan: Mapping[str, Any],
    control_token: str,
) -> Dict[str, Any]:
    """Verify a controller-signed human resolution before a new run starts."""
    token = _require_token(control_token)
    body = _resolution_attestation_body(attestation)
    if body.get("schema_version") != "2.1" or body.get("status") != "attested":
        raise ValueError("contract resolution attestation is not an attested V2.1 artifact")
    if body.get("proposed_contract_hash") != contract.get("contract_hash"):
        raise ValueError("resolution attestation does not belong to the contract")
    if body.get("proposed_plan_hash") != plan.get("plan_hash"):
        raise ValueError("resolution attestation does not belong to the plan")
    if plan.get("contract_hash") != contract.get("contract_hash"):
        raise ValueError("plan does not belong to the resolved contract")
    human = body.get("human_attestation") or {}
    if not isinstance(human, Mapping):
        raise ValueError("human_attestation must be an object")
    if human.get("approved_delta_hash") != body.get("delta_hash"):
        raise ValueError("human attestation did not approve this contract delta")
    if not str(human.get("attested_by") or "").strip() or not str(
        human.get("evidence") or ""
    ).strip():
        raise ValueError("human attestation requires attested_by and evidence")
    if not str(body.get("external_task_ref") or "").strip():
        raise ValueError("resolution attestation must bind the external task")
    if body.get("control_token_sha256") != hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest():
        raise ValueError("resolution attestation names a different controller token")

    attestation_hash = str(attestation.get("attestation_hash") or "")
    if attestation_hash != _sha256(body):
        raise ValueError("contract resolution attestation hash is invalid")
    expected = hmac.new(
        token.encode("utf-8"), attestation_hash.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(
        str(attestation.get("controller_signature") or ""), expected
    ):
        raise ValueError("contract resolution controller signature is invalid")
    return copy.deepcopy(dict(attestation))


def attest_contract_resolution(
    proposal: Mapping[str, Any],
    parent_contract: Mapping[str, Any],
    proposed_plan: Mapping[str, Any],
    human_attestation: Mapping[str, Any],
    control_token: str,
    external_task_ref: str,
    parent_ledger: Optional[Mapping[str, Any]] = None,
    parent_plan: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Bind a human-approved delta to the same task and the next plan.

    This is deliberately a trusted-controller operation and is not exposed by
    the stateless governance MCP.  The signature proves which local controller
    recorded the attestation; identity proof remains the human surface's job.
    """
    token = _require_token(control_token)
    if not isinstance(proposal, Mapping):
        raise ValueError("contract resolution proposal must be an object")
    if proposal.get("status") != "awaiting_human_attestation":
        raise ValueError("contract resolution proposal is not awaiting attestation")
    delta = proposal.get("delta") or {}
    if not isinstance(delta, Mapping) or proposal.get("delta_hash") != _sha256(delta):
        raise ValueError("contract resolution proposal delta is invalid")
    if delta.get("parent_contract_hash") != parent_contract.get("contract_hash"):
        raise ValueError("contract resolution proposal does not belong to the parent")
    proposed_contract = proposal.get("proposed_contract") or {}
    if not isinstance(proposed_contract, Mapping):
        raise ValueError("proposed_contract must be an object")
    if delta.get("proposed_contract_hash") != proposed_contract.get("contract_hash"):
        raise ValueError("contract resolution delta does not bind the proposed contract")
    if proposed_contract.get("status") != "ready":
        raise ValueError("resolved contract must be ready before owner creation")

    # Recompile integrity through the public governance boundary. This does not
    # run an Agent or mutate external state.
    from .governance import GovernanceEngine

    engine = GovernanceEngine()
    engine.route(parent_contract)
    handoff = engine.delivery_handoff(proposed_contract, proposed_plan)
    if handoff.get("status") != "ready_for_control_plane":
        raise ValueError("resolved contract and plan are not ready for the control plane")

    if not isinstance(human_attestation, Mapping):
        raise ValueError("human_attestation must be an object")
    approved_delta_hash = _short_text(
        human_attestation.get("approved_delta_hash"),
        "approved_delta_hash",
        required=True,
        limit=64,
    )
    if approved_delta_hash != proposal.get("delta_hash"):
        raise ValueError("human attestation approved a different contract delta")
    human = {
        "attested_by": _short_text(
            human_attestation.get("attested_by"),
            "attested_by",
            required=True,
            limit=200,
        ),
        "authority": _short_text(
            human_attestation.get("authority"),
            "attestation authority",
            limit=80,
        )
        or "human",
        "evidence": _short_text(
            human_attestation.get("evidence"),
            "attestation evidence",
            required=True,
            limit=4000,
        ),
        "approved_delta_hash": approved_delta_hash,
    }
    if human["authority"] != "human":
        raise ValueError("contract delta attestation requires human authority")
    task_ref = _short_text(
        external_task_ref, "external_task_ref", required=True, limit=300
    )

    if (parent_ledger is None) != (parent_plan is None):
        raise ValueError("parent_ledger and parent_plan must be supplied together")
    parent_run = {
        "run_id": "",
        "plan_hash": "",
        "latest_event_hash": "",
        "status": "not_started",
    }
    if parent_ledger is not None and parent_plan is not None:
        replay = replay_run_ledger(parent_ledger, parent_plan, token)
        if parent_ledger.get("contract_hash") != parent_contract.get("contract_hash"):
            raise ValueError("parent run does not belong to the parent contract")
        if replay.get("status") not in {
            "needs_human_decision",
            "awaiting_human_decision",
        }:
            raise ValueError("parent run must be paused at a human boundary")
        parent_run = {
            "run_id": str(parent_ledger.get("run_id") or ""),
            "plan_hash": str(parent_ledger.get("plan_hash") or ""),
            "latest_event_hash": str(
                (parent_ledger.get("events") or [{}])[-1].get("event_hash") or ""
            ),
            "status": str(parent_ledger.get("status") or ""),
        }

    body = {
        "schema_version": "2.1",
        "status": "attested",
        "parent_contract_hash": parent_contract.get("contract_hash"),
        "proposed_contract_hash": proposed_contract.get("contract_hash"),
        "proposed_plan_hash": proposed_plan.get("plan_hash"),
        "delta_hash": proposal.get("delta_hash"),
        "external_task_ref": task_ref,
        "parent_run": parent_run,
        "human_attestation": human,
        "attested_at": _timestamp(),
        "control_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
    }
    attestation_hash = _sha256(body)
    signature = hmac.new(
        token.encode("utf-8"), attestation_hash.encode("ascii"), hashlib.sha256
    ).hexdigest()
    attestation = {
        **body,
        "attestation_hash": attestation_hash,
        "controller_signature": signature,
    }
    return validate_contract_resolution_attestation(
        attestation, proposed_contract, proposed_plan, token
    )


def _event(
    ledger: Mapping[str, Any],
    event_type: str,
    stage: str,
    status: str,
    artifact_sha256: str,
    control_token: str,
    details: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    previous = (ledger.get("events") or [])[-1]["event_hash"] if ledger.get("events") else "GENESIS"
    unsigned = {
        "sequence": len(ledger.get("events") or []) + 1,
        "type": event_type,
        "stage": stage,
        "status": status,
        "artifact_sha256": artifact_sha256,
        "previous_event_hash": previous,
        "at": _timestamp(),
        "details": copy.deepcopy(dict(details or {})),
    }
    event_hash = _sha256(unsigned)
    signature = hmac.new(
        control_token.encode("utf-8"), event_hash.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return {**unsigned, "event_hash": event_hash, "controller_signature": signature}


def create_run_ledger(
    contract: Mapping[str, Any],
    plan: Mapping[str, Any],
    control_token: str,
    run_id: str = "",
    resolution_attestation: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a signed checkpoint ledger without persisting the controller token."""
    token = _require_token(control_token)
    if not contract.get("contract_hash") or not plan.get("plan_hash"):
        raise ValueError("compiled contract_hash and plan_hash are required")
    if plan.get("contract_hash") != contract.get("contract_hash"):
        raise ValueError("plan does not belong to the contract")
    if contract.get("status") != "ready":
        raise ValueError("contract must be ready before owner creation")
    resolution = (
        validate_contract_resolution_attestation(
            resolution_attestation, contract, plan, token
        )
        if resolution_attestation is not None
        else {}
    )
    stages = list(plan.get("sequence") or [])
    if not stages:
        raise ValueError("plan sequence is required")
    selected_id = run_id.strip() or "run_%s" % secrets.token_hex(8)
    if not re.match(r"^[A-Za-z0-9_-]+$", selected_id):
        raise ValueError("run_id may contain only letters, numbers, '_' and '-'")
    now = _timestamp()
    ledger: Dict[str, Any] = {
        "schema_version": LEDGER_VERSION,
        "run_id": selected_id,
        "contract_id": contract.get("contract_id"),
        "contract_hash": contract["contract_hash"],
        "plan_hash": plan["plan_hash"],
        "control_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "status": "ready",
        "created_at": now,
        "updated_at": now,
        "stages": {
            stage: {"status": "pending", "artifacts": {}, "telemetry": {}}
            for stage in stages
        },
        "agents": {},
        "resolution_attestation": resolution,
        "events": [],
        "metrics": {
            "duration_ms": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "human_actions": 0,
            "control_plane_recoveries": 0,
        },
    }
    ledger["events"].append(
        _event(
            ledger,
            "ledger_created",
            "",
            "ready",
            _sha256(resolution),
            token,
            {
                "resolution_attestation_hash": resolution.get(
                    "attestation_hash", ""
                )
            },
        )
    )
    return ledger


def _short_text(value: Any, name: str, required: bool = False, limit: int = 2000) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError("%s must be a string" % name)
    result = value.strip()[:limit]
    if required and not result:
        raise ValueError("%s is required" % name)
    return result


def _normalize_agent_progress(
    value: Mapping[str, Any], plan: Mapping[str, Any], heartbeat_at: str = ""
) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("agent progress must be an object")
    agent_id = _short_text(value.get("agent_id"), "agent_id", required=True, limit=120)
    state = _short_text(
        value.get("execution_state"), "execution_state", required=True, limit=40
    )
    if state not in AGENT_EXECUTION_STATES:
        raise ValueError("unknown agent execution_state: %s" % state)
    enforcement_mode = _short_text(
        value.get("enforcement_mode"), "enforcement_mode", limit=40
    ) or "not_applicable"
    if enforcement_mode not in INSPECTOR_ENFORCEMENT_MODES:
        raise ValueError("unknown inspector enforcement_mode: %s" % enforcement_mode)
    stage = _short_text(value.get("stage"), "agent stage", required=True, limit=80)
    if stage not in set(plan.get("sequence") or []):
        raise ValueError("agent progress refers to an unknown stage: %s" % stage)
    source = value.get("source") or {}
    if not isinstance(source, Mapping):
        raise ValueError("agent progress source must be an object")
    display_name = _short_text(
        value.get("display_name"), "display_name", required=True, limit=80
    )
    if not re.search(r"[\u3400-\u9fff]", display_name):
        raise ValueError("display_name must contain a concise Chinese name")
    return {
        "agent_id": agent_id,
        "display_name": display_name,
        "mission": _short_text(value.get("mission"), "mission", required=True, limit=500),
        "stage": stage,
        "stage_name": STAGE_PRESENTATION.get(stage, stage),
        "enforcement_mode": enforcement_mode,
        "execution_state": state,
        "progress_summary": _short_text(
            value.get("progress_summary"), "progress_summary", required=True, limit=1000
        ),
        "current_difficulty": _short_text(
            value.get("current_difficulty"),
            "current_difficulty",
            required=True,
            limit=1000,
        ),
        "dependency": _short_text(value.get("dependency"), "dependency", limit=300),
        "needs_human": bool(value.get("needs_human", False)),
        "source": {
            "platform": _short_text(source.get("platform"), "source platform", limit=80),
            "task_id": _short_text(source.get("task_id"), "source task_id", limit=200),
            "session_id": _short_text(
                source.get("session_id"), "source session_id", limit=200
            ),
        },
        "last_heartbeat_at": heartbeat_at
        or _short_text(
            value.get("last_heartbeat_at"),
            "last_heartbeat_at",
            required=True,
            limit=80,
        ),
    }


def record_agent_progress(
    ledger: Mapping[str, Any],
    plan: Mapping[str, Any],
    progress: Mapping[str, Any],
    control_token: str,
) -> Dict[str, Any]:
    """Append one signed operator-facing progress event without advancing a stage."""
    token = _require_token(control_token)
    replay_run_ledger(ledger, plan, token)
    normalized = _normalize_agent_progress(progress, plan, _timestamp())
    updated = copy.deepcopy(dict(ledger))
    updated.setdefault("agents", {})[normalized["agent_id"]] = normalized
    updated["updated_at"] = normalized["last_heartbeat_at"]
    updated["events"].append(
        _event(
            updated,
            "agent_progress",
            normalized["stage"],
            str(updated.get("status")),
            _sha256(normalized),
            token,
            {"agent_update": normalized},
        )
    )
    return updated


def build_operator_snapshot(
    ledger: Mapping[str, Any], plan: Mapping[str, Any], control_token: str
) -> Dict[str, Any]:
    """Project signed runtime state into separate execution and verdict dimensions."""
    replay = replay_run_ledger(ledger, plan, control_token)
    agents = sorted(
        [copy.deepcopy(dict(item)) for item in (ledger.get("agents") or {}).values()],
        key=lambda item: (item.get("display_name", ""), item.get("agent_id", "")),
    )
    counts = {state: 0 for state in sorted(AGENT_EXECUTION_STATES)}
    for item in agents:
        state = str(item.get("execution_state"))
        if state in counts:
            counts[state] += 1
    if ledger.get("status") == "needs_human_decision":
        verdict = "blocked"
    elif (ledger.get("stages") or {}).get("blind_final_verification", {}).get("status") == "completed":
        verdict = "pass_pending_human"
    else:
        verdict = "unreviewed"
    return {
        "schema_version": LEDGER_VERSION,
        "run_id": ledger.get("run_id"),
        "execution": {
            "status": ledger.get("status"),
            "current_stage": replay.get("next_stage"),
            "current_stage_name": STAGE_PRESENTATION.get(
                str(replay.get("next_stage") or ""), "已完成全部自动阶段"
            ),
            "unique_agents": len(agents),
            "counts": counts,
        },
        "delivery_verdict": verdict,
        "agents": agents,
        "completed_stages": [
            {"id": stage, "name": STAGE_PRESENTATION.get(stage, stage)}
            for stage in replay.get("completed_stages") or []
        ],
        "needs_human": bool(
            ledger.get("status") in {"needs_human_decision", "awaiting_human_decision"}
            or any(item.get("needs_human") for item in agents)
        ),
        "stop": copy.deepcopy(ledger.get("stop") or {}),
    }


def build_human_review_packet(
    ledger: Mapping[str, Any], plan: Mapping[str, Any], control_token: str
) -> Dict[str, Any]:
    """Build a compact, evidence-addressed handoff packet for a human reviewer."""
    snapshot = build_operator_snapshot(ledger, plan, control_token)
    stage_evidence = []
    for stage in plan.get("sequence") or []:
        record = (ledger.get("stages") or {}).get(stage) or {}
        if record.get("status") != "completed":
            continue
        stage_evidence.append(
            {
                "stage": stage,
                "stage_name": STAGE_PRESENTATION.get(stage, stage),
                "artifact_sha256": record.get("artifact_sha256"),
                "completed_at": record.get("completed_at"),
                "telemetry": copy.deepcopy(record.get("telemetry") or {}),
            }
        )
    adjudication = (
        ((ledger.get("stages") or {}).get("adjudication") or {})
        .get("artifacts", {})
        .get("adjudication", {})
    )
    if not isinstance(adjudication, Mapping):
        adjudication = {}
    inspector_metrics = ((adjudication.get("metrics") or {}).get("per_inspector") or {})
    if not isinstance(inspector_metrics, Mapping):
        inspector_metrics = {}
    calibration = [
        {
            "lane_id": str(lane_id),
            "enforcement_mode": str((metrics or {}).get("enforcement_mode") or ""),
            "calibration_profile_hash": str(
                (metrics or {}).get("calibration_profile_hash") or ""
            ),
            "submitted_findings": int((metrics or {}).get("submitted_findings", 0) or 0),
            "independent_defect_contributions": int(
                (metrics or {}).get("independent_defect_contributions", 0) or 0
            ),
        }
        for lane_id, metrics in sorted(inspector_metrics.items())
        if isinstance(metrics, Mapping)
    ]
    if ledger.get("status") == "awaiting_human_decision":
        decision_requested = "accept_or_request_changes"
    elif ledger.get("status") == "needs_human_decision":
        decision_requested = "resolve_blocker_or_stop"
    else:
        decision_requested = "none_yet"
    return {
        "schema_version": LEDGER_VERSION,
        "run_id": ledger.get("run_id"),
        "contract_id": ledger.get("contract_id"),
        "contract_hash": ledger.get("contract_hash"),
        "plan_hash": ledger.get("plan_hash"),
        "resolution": {
            "attested": bool(ledger.get("resolution_attestation")),
            "attestation_hash": (
                ledger.get("resolution_attestation") or {}
            ).get("attestation_hash", ""),
            "parent_contract_hash": (
                ledger.get("resolution_attestation") or {}
            ).get("parent_contract_hash", ""),
            "external_task_ref": (
                ledger.get("resolution_attestation") or {}
            ).get("external_task_ref", ""),
        },
        "execution": snapshot["execution"],
        "delivery_verdict": snapshot["delivery_verdict"],
        "agents": snapshot["agents"],
        "stage_evidence": stage_evidence,
        "adjudication_summary": {
            "artifact_sha256": (
                (ledger.get("stages") or {}).get("adjudication") or {}
            ).get("artifact_sha256", ""),
            "decision": adjudication.get("decision", ""),
            "deterministic_blockers": len(
                adjudication.get("deterministic_blockers") or []
            ),
            "semantic_blockers": len(adjudication.get("semantic_blockers") or []),
            "shadow_findings": len(adjudication.get("shadow_findings") or []),
            "warnings": len(adjudication.get("warnings") or []),
            "rejected_findings": len(adjudication.get("rejected_findings") or []),
            "inspector_calibration": calibration,
        },
        "must_kill_case_ids": [
            str(item.get("id")) for item in plan.get("must_kill_cases") or []
        ],
        "human_attention": {
            "required": snapshot["needs_human"],
            "decision_requested": decision_requested,
            "agent_requests": [
                {
                    "agent_id": item.get("agent_id"),
                    "display_name": item.get("display_name"),
                    "current_difficulty": item.get("current_difficulty"),
                }
                for item in snapshot["agents"]
                if item.get("needs_human")
            ],
            "stop": snapshot["stop"],
            "external_actions_allowed": False,
            "automated_approval": False,
        },
    }


def _required_semantic_lanes(plan: Mapping[str, Any]) -> List[str]:
    return [
        str(lane.get("id"))
        for lane in plan.get("lanes") or []
        if lane.get("kind") != "deterministic"
    ]


def _prior_artifacts(ledger: Mapping[str, Any], stage: str) -> Mapping[str, Any]:
    record = (ledger.get("stages") or {}).get(stage) or {}
    artifacts = record.get("artifacts") or {}
    return artifacts if isinstance(artifacts, Mapping) else {}


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and bool(HEX_64.match(value))


def validate_stage_artifacts(
    ledger: Mapping[str, Any],
    plan: Mapping[str, Any],
    stage: str,
    artifacts: Mapping[str, Any],
) -> List[str]:
    """Validate observed artifacts, never an agent's prose completion claim."""
    errors: List[str] = []
    contract_hash = ledger.get("contract_hash")
    plan_hash = ledger.get("plan_hash")

    if stage == "environment_preflight":
        capsule = artifacts.get("environment_capsule")
        if not isinstance(capsule, Mapping):
            return ["environment_capsule is required"]
        if capsule.get("status") != "ready" or capsule.get("blockers"):
            errors.append("environment preflight is not ready")
        if capsule.get("contract_hash") != contract_hash:
            errors.append("environment capsule is not bound to the contract")
        if capsule.get("repository_root") != capsule.get("diff_root"):
            errors.append("the real diff root differs from the assigned repository root")

    elif stage == "owner_implementation":
        report = artifacts.get("owner_report")
        if not isinstance(report, Mapping):
            return ["owner_report is required"]
        if not str(report.get("owner_context_id") or "").strip():
            errors.append("owner_context_id is required")
        if not _is_digest(report.get("diff_sha256")):
            errors.append("diff_sha256 must be a sha256 digest")
        if report.get("external_actions_performed"):
            errors.append("owner performed an external action")

    elif stage in {"deterministic_ci", "full_reverification"}:
        results = artifacts.get("deterministic_results")
        if not isinstance(results, list) or not results:
            return ["deterministic_results are required"]
        observed = set()
        observed_classes = set()
        for result in results:
            if not isinstance(result, Mapping):
                errors.append("deterministic result must be an object")
                continue
            status = str(result.get("status") or "").lower()
            if bool(result.get("required", True)) and status not in PASS_STATUSES:
                errors.append("required deterministic result did not pass: %s" % result.get("name"))
            observed.update(
                str(value).casefold()
                for value in (result.get("name"), result.get("command"))
                if value
            )
            if result.get("class"):
                observed_classes.add(str(result["class"]).casefold())
            if not result.get("evidence") and not result.get("evidence_sha256"):
                errors.append("deterministic result lacks evidence: %s" % result.get("name"))
        for expected in plan.get("required_checks") or []:
            if str(expected).casefold() not in observed:
                errors.append("required deterministic evidence is missing: %s" % expected)
        for expected_class in plan.get("required_evidence_classes") or []:
            if str(expected_class).casefold() not in observed_classes:
                errors.append("required evidence class is missing: %s" % expected_class)
        if stage == "full_reverification" and artifacts.get("full_plan_rerun") is not True:
            errors.append("full re-verification must rerun the complete plan")

    elif stage == "independent_inspection":
        packets = artifacts.get("inspection_packets")
        if not isinstance(packets, list):
            return ["inspection_packets are required"]
        by_lane = {
            str(packet.get("lane_id")): packet
            for packet in packets
            if isinstance(packet, Mapping)
        }
        for lane_id in _required_semantic_lanes(plan):
            packet = by_lane.get(lane_id)
            if packet is None:
                errors.append("inspection packet is missing for lane: %s" % lane_id)
                continue
            if packet.get("fresh_context") is not True or packet.get("read_only") is not True:
                errors.append("inspector isolation is unproven for lane: %s" % lane_id)
            if packet.get("developer_transcript_visible") is not False:
                errors.append("owner transcript leaked to lane: %s" % lane_id)
            if packet.get("peer_findings_visible") is not False:
                errors.append("peer findings leaked to lane: %s" % lane_id)
            if not _is_digest(packet.get("evidence_bundle_sha256")):
                errors.append("evidence bundle digest is missing for lane: %s" % lane_id)

    elif stage == "adjudication":
        verdict = artifacts.get("adjudication")
        if not isinstance(verdict, Mapping):
            return ["adjudication is required"]
        if verdict.get("contract_hash") != contract_hash or verdict.get("plan_hash") != plan_hash:
            errors.append("adjudication is not bound to this contract and plan")
        if verdict.get("decision") not in {
            "repair_once", "ready_for_final_verification", "human_decision", "needs_clarification"
        }:
            errors.append("adjudication decision is invalid")

    elif stage == "single_consolidated_repair":
        verdict = _prior_artifacts(ledger, "adjudication").get("adjudication") or {}
        if verdict.get("decision") == "repair_once":
            repair = artifacts.get("repair_report")
            if not isinstance(repair, Mapping):
                return ["repair_report is required after repair_once"]
            original = _prior_artifacts(ledger, "owner_implementation").get("owner_report") or {}
            if repair.get("owner_context_id") != original.get("owner_context_id"):
                errors.append("repair did not return to the original owner context")
            if repair.get("round") != 1:
                errors.append("only repair round 1 is allowed")
            if not _is_digest(repair.get("diff_sha256")):
                errors.append("repair diff_sha256 is required")
        elif artifacts.get("skipped") is not True:
            errors.append("repair stage must be explicitly skipped when no repair is required")

    elif stage == "blind_final_verification":
        report = artifacts.get("final_verifier_report")
        if not isinstance(report, Mapping):
            return ["final_verifier_report is required"]
        if report.get("status") not in PASS_STATUSES:
            errors.append("blind final verifier did not pass")
        if report.get("fresh_context") is not True or report.get("read_only") is not True:
            errors.append("final verifier is not fresh and read-only")
        if report.get("blind_to_owner_transcript") is not True:
            errors.append("final verifier saw the owner transcript")
        if report.get("blind_to_prior_findings") is not True:
            errors.append("final verifier saw prior inspector findings")
        results = report.get("must_kill_results") or []
        passed_ids = {
            str(item.get("id"))
            for item in results
            if isinstance(item, Mapping) and str(item.get("status") or "").lower() in PASS_STATUSES
        }
        for case in plan.get("must_kill_cases") or []:
            if str(case.get("id")) not in passed_ids:
                errors.append("must-kill case did not pass: %s" % case.get("id"))

    elif stage == "human_handoff":
        handoff = artifacts.get("human_handoff")
        if not isinstance(handoff, Mapping):
            return ["human_handoff is required"]
        if handoff.get("automated_approval") is not False:
            errors.append("the workflow must not approve its own delivery")
        if handoff.get("status") != "awaiting_human_decision":
            errors.append("handoff must stop at awaiting_human_decision")

    else:
        errors.append("unknown V2 stage: %s" % stage)
    return errors


def _normalized_telemetry(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    numeric = ("duration_ms", "input_tokens", "output_tokens", "human_actions", "control_plane_recoveries")
    result = {name: max(0, int(raw.get(name, 0) or 0)) for name in numeric}
    result["inspector_contributions"] = list(raw.get("inspector_contributions") or [])[:100]
    return result


def advance_run_ledger(
    ledger: Mapping[str, Any],
    plan: Mapping[str, Any],
    stage: str,
    artifacts: Mapping[str, Any],
    control_token: str,
    telemetry: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Advance exactly one stage after its observable artifact invariants pass."""
    token = _require_token(control_token)
    replay_run_ledger(ledger, plan, token)
    if ledger.get("status") != "ready":
        raise ValueError("ledger is stopped and cannot advance automatically")
    stages = list(plan.get("sequence") or [])
    pending = [name for name in stages if (ledger.get("stages") or {}).get(name, {}).get("status") == "pending"]
    if not pending or pending[0] != stage:
        raise ValueError("stage transition is out of order; next stage is %s" % (pending[0] if pending else "none"))
    if not isinstance(artifacts, Mapping):
        raise ValueError("artifacts must be an object")
    errors = validate_stage_artifacts(ledger, plan, stage, artifacts)
    if errors:
        raise ValueError("artifact invariants failed: %s" % "; ".join(errors))

    updated = copy.deepcopy(dict(ledger))
    stage_telemetry = _normalized_telemetry(telemetry)
    updated["stages"][stage] = {
        "status": "completed",
        "artifacts": copy.deepcopy(dict(artifacts)),
        "artifact_sha256": _sha256(artifacts),
        "telemetry": stage_telemetry,
        "completed_at": _timestamp(),
    }
    for name in ("duration_ms", "input_tokens", "output_tokens", "human_actions", "control_plane_recoveries"):
        updated["metrics"][name] += stage_telemetry[name]

    decision = ((artifacts.get("adjudication") or {}).get("decision") if stage == "adjudication" else "")
    if decision in {"human_decision", "needs_clarification"}:
        updated["status"] = "needs_human_decision"
    elif stage == "human_handoff":
        updated["status"] = "awaiting_human_decision"
    updated["updated_at"] = _timestamp()
    updated["events"].append(
        _event(
            updated,
            "stage_completed",
            stage,
            updated["status"],
            updated["stages"][stage]["artifact_sha256"],
            token,
            {"telemetry_sha256": _sha256(stage_telemetry)},
        )
    )
    return updated


def stop_run_ledger(
    ledger: Mapping[str, Any],
    plan: Mapping[str, Any],
    reason: str,
    evidence: str,
    control_token: str,
) -> Dict[str, Any]:
    """Record an honest fail-closed stop instead of manufacturing completion."""
    token = _require_token(control_token)
    replay_run_ledger(ledger, plan, token)
    if reason not in STOP_REASONS:
        raise ValueError("unknown stop reason")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("stop evidence is required")
    updated = copy.deepcopy(dict(ledger))
    updated["status"] = "needs_human_decision"
    updated["stop"] = {"reason": reason, "evidence": evidence.strip()[:8000], "at": _timestamp()}
    updated["updated_at"] = _timestamp()
    updated["events"].append(
        _event(updated, "honest_stop", "", updated["status"], _sha256(updated["stop"]), token, {"reason": reason})
    )
    return updated


def replay_run_ledger(
    ledger: Mapping[str, Any], plan: Mapping[str, Any], control_token: str
) -> Dict[str, Any]:
    """Verify signatures, hash chaining, stage order, artifacts, and resume point."""
    token = _require_token(control_token)
    if ledger.get("schema_version") != LEDGER_VERSION:
        raise ValueError("unsupported ledger schema_version")
    if ledger.get("plan_hash") != plan.get("plan_hash"):
        raise ValueError("ledger does not belong to the supplied plan")
    if ledger.get("contract_hash") != plan.get("contract_hash"):
        raise ValueError("ledger contract does not belong to the supplied plan")
    if ledger.get("contract_id") != plan.get("contract_id"):
        raise ValueError("ledger contract id does not belong to the supplied plan")
    if ledger.get("control_token_sha256") != hashlib.sha256(token.encode("utf-8")).hexdigest():
        raise ValueError("controller attestation failed")
    previous = "GENESIS"
    verified_events: List[Dict[str, Any]] = []
    for index, raw in enumerate(ledger.get("events") or []):
        event = dict(raw)
        signature = event.pop("controller_signature", "")
        event_hash = event.pop("event_hash", "")
        if event.get("sequence") != index + 1 or event.get("previous_event_hash") != previous:
            raise ValueError("ledger event chain is broken")
        calculated = _sha256(event)
        if event_hash != calculated:
            raise ValueError("ledger event hash is invalid")
        expected_signature = hmac.new(
            token.encode("utf-8"), event_hash.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("controller event signature is invalid")
        previous = event_hash
        verified_events.append({**event, "event_hash": event_hash})

    resolution = ledger.get("resolution_attestation") or {}
    if resolution:
        validate_contract_resolution_attestation(
            resolution,
            {"contract_hash": ledger.get("contract_hash")},
            plan,
            token,
        )
    creation_events = [
        event for event in verified_events if event.get("type") == "ledger_created"
    ]
    if len(creation_events) != 1 or creation_events[0].get("sequence") != 1:
        raise ValueError("ledger creation is not bound to exactly one signed event")
    if creation_events[0].get("artifact_sha256") != _sha256(resolution):
        raise ValueError("ledger resolution differs from its signed creation event")
    if (creation_events[0].get("details") or {}).get(
        "resolution_attestation_hash"
    ) != resolution.get("attestation_hash", ""):
        raise ValueError("ledger resolution hash differs from its signed creation event")

    reconstructed_agents: Dict[str, Dict[str, Any]] = {}
    for event in verified_events:
        if event.get("type") != "agent_progress":
            continue
        details = event.get("details") or {}
        update = details.get("agent_update") if isinstance(details, Mapping) else None
        if not isinstance(update, Mapping):
            raise ValueError("agent progress event is missing its signed update")
        normalized = _normalize_agent_progress(update, plan)
        if event.get("artifact_sha256") != _sha256(normalized):
            raise ValueError("agent progress differs from its signed event")
        reconstructed_agents[normalized["agent_id"]] = normalized
    if dict(ledger.get("agents") or {}) != reconstructed_agents:
        raise ValueError("operator agent snapshot differs from signed progress events")

    if not verified_events or verified_events[-1].get("status") != ledger.get("status"):
        raise ValueError("ledger status is not bound to the latest signed event")

    stages = list(plan.get("sequence") or [])
    completed = []
    pending_seen = False
    for stage in stages:
        record = (ledger.get("stages") or {}).get(stage)
        if not isinstance(record, Mapping):
            raise ValueError("ledger stage is missing: %s" % stage)
        status = record.get("status")
        if status == "completed":
            if pending_seen:
                raise ValueError("ledger contains an out-of-order completed stage")
            if record.get("artifact_sha256") != _sha256(record.get("artifacts") or {}):
                raise ValueError("stage artifact digest is invalid: %s" % stage)
            stage_events = [
                event
                for event in verified_events
                if event.get("type") == "stage_completed" and event.get("stage") == stage
            ]
            if len(stage_events) != 1:
                raise ValueError("completed stage is not bound to exactly one signed event: %s" % stage)
            if stage_events[0].get("artifact_sha256") != record.get("artifact_sha256"):
                raise ValueError("stage artifact differs from its signed event: %s" % stage)
            expected_telemetry = _sha256(record.get("telemetry") or {})
            if (stage_events[0].get("details") or {}).get("telemetry_sha256") != expected_telemetry:
                raise ValueError("stage telemetry differs from its signed event: %s" % stage)
            errors = validate_stage_artifacts(ledger, plan, stage, record.get("artifacts") or {})
            if errors:
                raise ValueError("stored stage artifact invariants failed: %s" % "; ".join(errors))
            completed.append(stage)
        elif status == "pending":
            if any(
                event.get("type") == "stage_completed" and event.get("stage") == stage
                for event in verified_events
            ):
                raise ValueError("pending stage has a signed completion event: %s" % stage)
            pending_seen = True
        else:
            raise ValueError("invalid ledger stage status: %s" % status)
    next_stage = next((name for name in stages if name not in completed), None)
    return {
        "valid": True,
        "run_id": ledger.get("run_id"),
        "status": ledger.get("status"),
        "completed_stages": completed,
        "next_stage": next_stage,
    }


class AtomicLedgerStore:
    """Atomically persist a caller-owned ledger with a single-writer lock."""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        self._lock_handle = None

    def __enter__(self) -> "AtomicLedgerStore":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock_handle = lock.open("a+", encoding="utf-8")
        if fcntl is not None:
            try:
                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self._lock_handle.close()
                self._lock_handle = None
                raise RuntimeError("another controller is advancing this ledger") from exc
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        if self._lock_handle is not None:
            if fcntl is not None:
                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None

    def load(self) -> Dict[str, Any]:
        with self.path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("ledger file must contain an object")
        return value

    def save(self, ledger: Mapping[str, Any]) -> None:
        if self._lock_handle is None:
            raise RuntimeError("AtomicLedgerStore must be used as a context manager")
        descriptor, raw_path = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        temp_path = Path(raw_path)
        try:
            os.chmod(raw_path, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(ledger, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temp_path), str(self.path))
        finally:
            if temp_path.exists():
                temp_path.unlink()


def _git(repo: Path, arguments: Sequence[str]) -> Tuple[int, str]:
    try:
        completed = subprocess.run(
            ["git"] + list(arguments),
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    # Preserve leading porcelain status columns; callers that consume a scalar
    # Git value tolerate the trailing newline being removed.
    return completed.returncode, (completed.stdout or completed.stderr or "").rstrip()


def _command_candidates(contract: Mapping[str, Any]) -> List[str]:
    environment = contract.get("environment") or {}
    candidates = list(environment.get("required_commands") or []) if isinstance(environment, Mapping) else []
    for check in contract.get("deterministic_checks") or []:
        try:
            first = shlex.split(str(check))[0]
        except (ValueError, IndexError):
            continue
        if re.match(r"^[A-Za-z0-9_.+-]+$", first) and (" " in str(check) or first in {"git", "python", "python3", "npm", "pnpm", "pytest"}):
            candidates.append(first)
    result: List[str] = []
    for candidate in candidates:
        value = str(candidate).strip()
        if value and value not in result:
            result.append(value)
    return result


def build_environment_capsule(
    repo: Path, contract: Mapping[str, Any]
) -> Dict[str, Any]:
    """Inspect cwd, permissions, PATH, ports, locks, and the real Git diff root."""
    root = Path(repo).expanduser().resolve()
    blockers: List[str] = []
    warnings: List[str] = []
    if not root.is_dir():
        raise ValueError("repository path must be an existing directory")
    code, top = _git(root, ["rev-parse", "--show-toplevel"])
    diff_root = str(Path(top).resolve()) if code == 0 and top else ""
    if not diff_root:
        blockers.append("not_a_git_worktree")
    elif diff_root != str(root):
        blockers.append("assigned_repo_is_not_real_diff_root")
    code, git_dir_raw = _git(root, ["rev-parse", "--git-dir"])
    git_dir = ""
    if code == 0 and git_dir_raw:
        candidate = Path(git_dir_raw)
        git_dir = str((root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve())
    locks = []
    if git_dir and (Path(git_dir) / "index.lock").exists():
        locks.append("git_index_lock")
        blockers.append("git_index_lock_present")

    commands = []
    for name in _command_candidates(contract):
        resolved = shutil.which(name)
        commands.append({"name": name, "available": bool(resolved), "resolved_path": resolved or ""})
        if not resolved:
            blockers.append("missing_command:%s" % name)

    environment = contract.get("environment") or {}
    ports = []
    for raw_port in (environment.get("required_ports") or []) if isinstance(environment, Mapping) else []:
        port = int(raw_port)
        if port < 1 or port > 65535:
            blockers.append("invalid_port:%s" % raw_port)
            continue
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.15)
        try:
            in_use = probe.connect_ex(("127.0.0.1", port)) == 0
        finally:
            probe.close()
        ports.append({"port": port, "available_for_bind": not in_use})
        if in_use:
            blockers.append("port_in_use:%d" % port)

    code, status_raw = _git(root, ["status", "--porcelain=v1", "--untracked-files=all"])
    if code != 0:
        blockers.append("git_status_unavailable")
        changed_files: List[str] = []
    else:
        changed_files = [line[3:] for line in status_raw.splitlines() if len(line) >= 4]
    if not os.access(str(root), os.R_OK):
        blockers.append("repository_not_readable")
    if not os.access(str(root), os.W_OK):
        blockers.append("repository_not_writable_for_owner")
    path_value = os.environ.get("PATH", "")
    capsule: Dict[str, Any] = {
        "schema_version": LEDGER_VERSION,
        "contract_hash": contract.get("contract_hash"),
        "repository_root": str(root),
        "diff_root": diff_root,
        "process_cwd": str(Path.cwd().resolve()),
        "permissions": {
            "readable": os.access(str(root), os.R_OK),
            "writable": os.access(str(root), os.W_OK),
        },
        "path": {
            "entry_count": len([item for item in path_value.split(os.pathsep) if item]),
            "sha256": hashlib.sha256(path_value.encode("utf-8")).hexdigest(),
        },
        "commands": commands,
        "ports": ports,
        "locks": locks,
        "git": {"git_dir": git_dir, "changed_files": changed_files},
        "warnings": warnings,
        "blockers": sorted(set(blockers)),
    }
    capsule["status"] = "blocked" if capsule["blockers"] else "ready"
    capsule["capsule_hash"] = _sha256(capsule)
    return capsule
