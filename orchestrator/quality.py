"""Deterministic quality policy for AI-native engineering delivery.

This module is intentionally stateless.  It selects verification dimensions,
builds isolation-aware lanes, aggregates evidence-backed findings and decides
release readiness.  It never runs a model, edits a repository, or stores a
second copy of LobeHub task state.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple


RISK_LEVELS = ("low", "medium", "high", "critical")
SEVERITIES = ("info", "low", "medium", "high", "critical")
CHANGE_SURFACES = (
    "docs",
    "ui",
    "api",
    "cli",
    "library",
    "data",
    "migration",
    "auth",
    "permissions",
    "billing",
    "dependency",
    "infrastructure",
    "background_job",
    "external_integration",
    "observability",
)


QUALITY_DIMENSIONS: Dict[str, Dict[str, Any]] = {
    "requirements": {
        "title": "Outcome and contract fidelity",
        "question": "Does the delivery prove every agreed outcome without scope drift?",
        "evidence": ("criterion traceability", "observable behavior", "negative requirements"),
    },
    "architecture": {
        "title": "Architecture, data and compatibility",
        "question": "Does the change preserve module, data, API and dependency invariants?",
        "evidence": ("diff", "relevant architecture rules", "compatibility analysis"),
    },
    "test_quality": {
        "title": "Test adequacy and oracle quality",
        "question": "Would the tests fail for realistic regressions and meaningful mutations?",
        "evidence": ("tests", "changed behavior map", "counterexample or mutation reasoning"),
    },
    "security": {
        "title": "Security, privacy and permission boundaries",
        "question": "Is an exploitable path supported by attacker-controlled input and evidence?",
        "evidence": ("threat boundary", "data-flow trace", "deterministic scan or reproduction"),
    },
    "supply_chain": {
        "title": "Dependency and delivery-chain integrity",
        "question": "Did the change introduce vulnerable, unpinned or over-privileged dependencies?",
        "evidence": ("dependency diff", "lockfile or SBOM", "scanner output"),
    },
    "experience": {
        "title": "User experience, accessibility and end-to-end behavior",
        "question": "Does the real product surface deliver the agreed experience and failure states?",
        "evidence": ("real surface", "user flow", "DOM, screenshot, transcript or API evidence"),
    },
    "performance": {
        "title": "Performance and resource budgets",
        "question": "Does the delivery stay within declared latency, throughput and resource budgets?",
        "evidence": ("budget", "measurement", "baseline comparison"),
    },
    "observability": {
        "title": "Observability and diagnosability",
        "question": "Can success, failure, cost and recovery be observed without reading source code?",
        "evidence": ("signals", "failure event", "trace, log or metric mapping"),
    },
    "operations": {
        "title": "Operational safety and recovery",
        "question": "Can the change be deployed, migrated, interrupted and recovered safely?",
        "evidence": ("deployment shape", "migration behavior", "recovery rehearsal"),
    },
    "release": {
        "title": "Release, rollback and accountable decision",
        "question": "Are rollout, rollback, compatibility and human decision boundaries explicit?",
        "evidence": ("release plan", "rollback plan", "approval record"),
    },
}


def string_list(value: Any) -> List[str]:
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple)) else [value]
    result: List[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_surfaces(value: Any) -> List[str]:
    surfaces = string_list(value)
    unknown = sorted(set(surfaces) - set(CHANGE_SURFACES))
    if unknown:
        raise ValueError("unknown change surfaces: %s" % ", ".join(unknown))
    return surfaces


def risk_index(value: str) -> int:
    try:
        return RISK_LEVELS.index(value)
    except ValueError as exc:
        raise ValueError("risk must be one of: %s" % ", ".join(RISK_LEVELS)) from exc


def effective_risk(arguments: Mapping[str, Any]) -> str:
    risk = str(arguments.get("risk", "medium")).strip().lower()
    index = risk_index(risk)
    surfaces = set(normalize_surfaces(arguments.get("change_surfaces")))
    if arguments.get("irreversible") or arguments.get("security_sensitive"):
        index = max(index, 2)
    if arguments.get("external_effects"):
        index = max(index, 1)
    if surfaces & {"auth", "permissions", "billing", "migration"}:
        index = max(index, 2)
    if surfaces & {"data", "dependency", "infrastructure", "external_integration"}:
        index = max(index, 1)
    return RISK_LEVELS[min(index, len(RISK_LEVELS) - 1)]


def select_dimensions(contract: Mapping[str, Any]) -> List[str]:
    risk = str(contract.get("risk", "medium"))
    index = risk_index(risk)
    surfaces = set(normalize_surfaces(contract.get("change_surfaces")))
    selected: List[str] = ["requirements"]

    def add(name: str) -> None:
        if name not in selected:
            selected.append(name)

    if index >= 1 or surfaces - {"docs"}:
        add("architecture")
        add("test_quality")
        add("observability")
    if contract.get("security_sensitive") or index >= 2 or surfaces & {
        "auth", "permissions", "billing", "data", "external_integration"
    }:
        add("security")
    if surfaces & {"dependency", "infrastructure"}:
        add("supply_chain")
    if contract.get("subjective") or surfaces & {"ui", "api", "cli"}:
        add("experience")
    if contract.get("performance_budgets") or (
        index >= 2 and surfaces & {"api", "data", "background_job", "infrastructure"}
    ):
        add("performance")
    if contract.get("external_effects") or surfaces & {
        "migration", "infrastructure", "background_job", "external_integration"
    }:
        add("operations")
    if index >= 2 or contract.get("irreversible") or contract.get("external_effects"):
        add("release")
    return selected


def _lane(
    lane_id: str,
    dimensions: Sequence[str],
    blocking: bool = True,
    fresh_context: bool = True,
) -> Dict[str, Any]:
    return {
        "id": lane_id,
        "dimensions": list(dimensions),
        "blocking": blocking,
        "fresh_context": fresh_context,
        "read_only": True,
        "may_edit_delivery": False,
        "input_contract": [
            "frozen work contract",
            "change diff and relevant files",
            "repository quality rules",
            "deterministic gate evidence",
        ],
        "output_contract": {
            "format": "structured_findings",
            "minimum_confidence": 80,
            "requires_reproduction_or_concrete_evidence": True,
            "must_not_repeat_style_or_lint_noise": True,
        },
    }


def build_verification_plan(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    """Build a risk-adaptive verification DAG without launching any agent."""

    contract = arguments.get("contract") if isinstance(arguments.get("contract"), dict) else arguments
    risk = str(contract.get("risk") or effective_risk(contract))
    dimensions = select_dimensions(contract)
    surfaces = set(normalize_surfaces(contract.get("change_surfaces")))

    program_gates: List[Dict[str, Any]] = [
        {
            "id": "repository_checks",
            "blocking": True,
            "kind": "deterministic",
            "purpose": "Run repository-defined build, lint, type, unit and integration checks.",
            "acceptance_check": False,
        },
        {
            "id": "changeset_integrity",
            "blocking": True,
            "kind": "deterministic",
            "purpose": "Confirm the diff is scoped, contains no secrets and preserves required artifacts.",
            "acceptance_check": False,
        },
    ]
    if "security" in dimensions or "supply_chain" in dimensions:
        program_gates.append(
            {
                "id": "security_scanners",
                "blocking": True,
                "kind": "deterministic",
                "purpose": "Run configured static, dependency, secret and configuration scanners.",
                "acceptance_check": False,
            }
        )
    if surfaces & {"migration", "data"}:
        program_gates.append(
            {
                "id": "migration_compatibility",
                "blocking": True,
                "kind": "deterministic",
                "purpose": "Exercise forward/backward data compatibility and recovery fixtures.",
                "acceptance_check": False,
            }
        )

    index = risk_index(risk)
    lanes: List[Dict[str, Any]] = []
    if index == 0:
        lanes.append(_lane("owner_self_evidence", dimensions, fresh_context=False))
    elif index == 1:
        outcome_dimensions = [item for item in dimensions if item not in {"test_quality", "security", "supply_chain"}]
        lanes.append(_lane("outcome_and_design_falsification", outcome_dimensions))
        if "test_quality" in dimensions:
            lanes.append(_lane("test_oracle_falsification", ["test_quality"]))
        if "security" in dimensions or "supply_chain" in dimensions:
            lanes.append(_lane("security_falsification", [item for item in dimensions if item in {"security", "supply_chain"}]))
    else:
        groups: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
            ("outcome_falsification", ("requirements",)),
            ("architecture_falsification", ("architecture", "performance")),
            ("test_oracle_falsification", ("test_quality",)),
            ("security_falsification", ("security", "supply_chain")),
            ("experience_falsification", ("experience",)),
            ("operational_falsification", ("observability", "operations", "release")),
        )
        for lane_id, members in groups:
            selected = [item for item in members if item in dimensions]
            if selected:
                lanes.append(_lane(lane_id, selected))

    human_points: List[str] = ["final merge or release"]
    if contract.get("subjective"):
        human_points.append("experience and aesthetic acceptance")
    if contract.get("irreversible") or contract.get("external_effects"):
        human_points.append("before irreversible or external side effects")
    if index >= 2:
        human_points.append("before material execution when risk assumptions are unresolved")
    human_points.append("after one failed consolidated repair or verifier disagreement")

    return {
        "risk": risk,
        "dimensions": [dict({"id": item}, **QUALITY_DIMENSIONS[item]) for item in dimensions],
        "program_gates": program_gates,
        "independent_lanes": lanes,
        "human_decision_points": human_points,
        "repair_policy": {
            "owner": "original_delivery_context",
            "max_automatic_rounds": 1,
            "batch_findings": True,
            "rerun_all_program_gates": True,
            "rerun_failed_and_regression_sensitive_lanes": True,
        },
        "acceptance_policy": {
            "program_gates_are_preconditions_not_acceptance_checks": True,
            "acceptance_requires_observable_user_or_operator_outcomes": True,
            "immutable_evidence_rounds": True,
        },
    }


def _fingerprint(finding: Mapping[str, Any]) -> str:
    normalized = re.sub(r"\s+", " ", str(finding.get("summary", "")).strip().lower())
    location = str(finding.get("location", "")).strip().lower()
    dimension = str(finding.get("dimension", "")).strip().lower()
    raw = "\n".join((dimension, location, normalized))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _finding_evidence(finding: Mapping[str, Any]) -> List[str]:
    return string_list(finding.get("evidence"))


def aggregate_findings(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    """Deduplicate findings and choose pass, one repair, or escalation."""

    raw_findings = arguments.get("findings") or []
    if not isinstance(raw_findings, list):
        raise ValueError("findings must be an array")
    repair_rounds_used = int(arguments.get("repair_rounds_used", 0) or 0)
    required_lanes = set(string_list(arguments.get("required_lanes")))
    completed_lanes = set(string_list(arguments.get("completed_lanes")))
    missing_lanes = sorted(required_lanes - completed_lanes)

    deterministic_failures: List[Dict[str, Any]] = []
    for gate in arguments.get("program_gates") or []:
        if not isinstance(gate, dict):
            continue
        status = str(gate.get("status", "missing")).lower()
        if status not in {"passed", "pass", "success"}:
            deterministic_failures.append(dict(gate))

    kept: Dict[str, Dict[str, Any]] = {}
    filtered: List[Dict[str, Any]] = []
    needs_validation: List[Dict[str, Any]] = []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            filtered.append({"reason": "finding is not an object", "value": str(raw)})
            continue
        dimension = str(raw.get("dimension", "")).strip()
        severity = str(raw.get("severity", "medium")).strip().lower()
        confidence = int(raw.get("confidence", 0) or 0)
        reproducible = bool(raw.get("reproducible", False))
        evidence = _finding_evidence(raw)
        if dimension not in QUALITY_DIMENSIONS:
            filtered.append(dict(raw, filter_reason="unknown dimension"))
            continue
        if severity not in SEVERITIES:
            filtered.append(dict(raw, filter_reason="unknown severity"))
            continue
        if confidence < 80:
            filtered.append(dict(raw, filter_reason="confidence below 80"))
            continue
        item = dict(raw)
        item["dimension"] = dimension
        item["severity"] = severity
        item["fingerprint"] = str(raw.get("fingerprint") or _fingerprint(raw))
        item["confidence"] = confidence
        item["evidence"] = evidence
        if severity in {"high", "critical"} and not (reproducible or evidence):
            item["validation_reason"] = "blocking severity lacks reproduction or concrete evidence"
            needs_validation.append(item)
            continue
        key = item["fingerprint"]
        previous = kept.get(key)
        if previous:
            sources = string_list(previous.get("sources")) + string_list(item.get("source"))
            previous["sources"] = list(dict.fromkeys(sources))
            previous["confidence"] = max(int(previous.get("confidence", 0)), confidence)
            previous["evidence"] = list(dict.fromkeys(_finding_evidence(previous) + evidence))
        else:
            item["sources"] = string_list(item.get("source"))
            kept[key] = item

    findings = list(kept.values())
    blockers = [
        item
        for item in findings
        if item.get("severity") in {"high", "critical"}
        and item.get("introduced_by_change") is not False
    ]
    if missing_lanes:
        decision = "awaiting_evidence"
    elif needs_validation:
        decision = "escalate"
    elif deterministic_failures or blockers:
        decision = "repair_once" if repair_rounds_used < 1 else "escalate"
    elif arguments.get("human_gate_required"):
        decision = "ready_for_human_acceptance"
    else:
        decision = "pass"

    return {
        "decision": decision,
        "blocking_findings": blockers,
        "actionable_findings": findings,
        "needs_validation": needs_validation,
        "filtered_findings": filtered,
        "deterministic_failures": deterministic_failures,
        "missing_lanes": missing_lanes,
        "repair_rounds_used": repair_rounds_used,
        "max_automatic_repair_rounds": 1,
        "repair_owner": "original_delivery_context",
    }


def compare_contracts(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    baseline = arguments.get("baseline")
    candidate = arguments.get("candidate")
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise ValueError("baseline and candidate contracts are required")
    protected = (
        "goal",
        "user_outcome",
        "acceptance_criteria",
        "non_goals",
        "assumptions",
        "constraints",
        "prohibited_behaviors",
        "risk",
        "change_surfaces",
        "subjective",
        "irreversible",
        "external_effects",
        "security_sensitive",
        "security_boundaries",
        "rollback_plan",
        "observability_signals",
        "performance_budgets",
    )
    changes: List[Dict[str, Any]] = []
    for field in protected:
        before, after = baseline.get(field), candidate.get(field)
        if before != after:
            changes.append({"field": field, "before": before, "after": after})
    return {
        "drifted": bool(changes),
        "requires_human_decision": bool(changes),
        "changes": changes,
        "ignored_runtime_fields": ["topic_id", "session_id", "usage", "timestamps", "evidence"],
    }


def decide_release_readiness(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    contract = arguments.get("contract") or {}
    aggregate = arguments.get("verification") or {}
    if not isinstance(contract, dict) or not isinstance(aggregate, dict):
        raise ValueError("contract and verification must be objects")
    blockers: List[str] = []
    if contract.get("status") != "ready":
        blockers.append("work contract is not ready")
    if aggregate.get("decision") not in {"pass", "ready_for_human_acceptance"}:
        blockers.append("verification has not passed")
    if aggregate.get("blocking_findings") or aggregate.get("deterministic_failures"):
        blockers.append("blocking evidence remains")
    if aggregate.get("missing_lanes"):
        blockers.append("required verification lanes are incomplete")
    if (contract.get("external_effects") or contract.get("irreversible")) and not contract.get("rollback_plan"):
        blockers.append("rollback or compensating action is missing")
    if risk_index(str(contract.get("risk", "medium"))) >= 1 and not contract.get("observability_signals"):
        blockers.append("success and failure observability signals are missing")

    human_required = bool(
        contract.get("subjective")
        or contract.get("external_effects")
        or contract.get("irreversible")
        or risk_index(str(contract.get("risk", "medium"))) >= 2
    )
    human_approved = bool(arguments.get("human_approved", False))
    if blockers:
        status = "blocked"
    elif human_required and not human_approved:
        status = "needs_human_decision"
    elif arguments.get("release_requested"):
        status = "ready_for_release"
    else:
        status = "ready_for_local_delivery"
    return {
        "status": status,
        "blockers": blockers,
        "human_decision_required": human_required,
        "human_approved": human_approved,
        "external_action_performed": False,
    }


def policy_digest(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
