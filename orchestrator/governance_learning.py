"""Pure calibration and Bad Case compilation for governance V2.3.

This module never runs an evaluator, persists a registry, or promotes a case on
its own.  It compiles human-owned evidence into hash-bound artifacts that an
external trusted controller can version and enforce.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence


LEARNING_VERSION = "2.3"
CASE_STATUSES = frozenset({"candidate", "confirmed", "retired"})
CASE_SEVERITIES = frozenset({"low", "medium", "high", "critical"})
EVIDENCE_CLASSES = frozenset(
    {"property_test", "mutation_test", "browser_e2e", "environment_fault", "semantic_oracle"}
)
CALIBRATION_MODES = frozenset({"shadow", "blocking"})
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_hash(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _text(value: Any, name: str, required: bool = False, limit: int = 4000) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError("%s must be a string" % name)
    result = value.strip()[:limit]
    if required and not result:
        raise ValueError("%s is required" % name)
    return result


def _objects(value: Any, name: str, limit: int = 500) -> List[Mapping[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ValueError("%s must be an array of objects" % name)
    result = []
    for raw in list(value)[:limit]:
        if not isinstance(raw, Mapping):
            raise ValueError("%s entries must be objects" % name)
        result.append(raw)
    return result


def _strings(value: Any, name: str, limit: int = 50) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ValueError("%s must be an array of strings" % name)
    result = []
    seen = set()
    for raw in list(value)[:limit]:
        item = _text(raw, "%s entry" % name, required=True, limit=500)
        if item.casefold() not in seen:
            seen.add(item.casefold())
            result.append(item)
    return result


def compile_bad_case_registry(source: Mapping[str, Any]) -> Dict[str, Any]:
    """Compile expert-confirmed failures into a hidden, hash-bound registry."""
    if not isinstance(source, Mapping):
        raise ValueError("bad case registry source must be an object")
    registry_id = _text(source.get("registry_id"), "registry_id", required=True, limit=120)
    cases = []
    seen = set()
    for index, raw in enumerate(_objects(source.get("cases"), "cases")):
        case_id = _text(raw.get("id"), "case id", limit=120) or "case-%d" % (index + 1)
        if case_id in seen:
            raise ValueError("duplicate bad case id: %s" % case_id)
        seen.add(case_id)
        status = _text(raw.get("status"), "case status", limit=30) or "candidate"
        if status not in CASE_STATUSES:
            raise ValueError("unknown bad case status: %s" % status)
        severity = _text(raw.get("severity"), "case severity", limit=30) or "high"
        if severity not in CASE_SEVERITIES:
            raise ValueError("unknown bad case severity: %s" % severity)
        evidence_classes = _strings(raw.get("evidence_classes"), "evidence_classes")
        unknown_classes = sorted(set(evidence_classes) - EVIDENCE_CLASSES)
        if unknown_classes:
            raise ValueError("unknown bad case evidence class: %s" % ", ".join(unknown_classes))
        confirmed_by = _text(raw.get("confirmed_by"), "confirmed_by", limit=200)
        confirmation_evidence = _text(
            raw.get("confirmation_evidence"), "confirmation_evidence", limit=4000
        )
        hidden_from_owner = bool(raw.get("hidden_from_owner", True))
        if status == "confirmed" and (not confirmed_by or not confirmation_evidence):
            raise ValueError(
                "confirmed bad cases require confirmed_by and confirmation_evidence"
            )
        if status == "confirmed" and not hidden_from_owner:
            raise ValueError(
                "confirmed registry cases must remain hidden_from_owner"
            )
        normalized = {
            "id": case_id,
            "title": _text(raw.get("title"), "case title", required=True, limit=500),
            "category": _text(
                raw.get("category"), "case category", required=True, limit=120
            ),
            "counterexample": _text(
                raw.get("counterexample"), "case counterexample", required=True, limit=8000
            ),
            "expected": _text(
                raw.get("expected"), "case expected", required=True, limit=4000
            ),
            "severity": severity,
            "status": status,
            "source": _text(raw.get("source"), "case source", required=True, limit=1000),
            "evidence_classes": evidence_classes,
            "confirmed_by": confirmed_by,
            "confirmation_evidence": confirmation_evidence,
            "hidden_from_owner": hidden_from_owner,
            "regression_test": _text(
                raw.get("regression_test"), "regression_test", limit=2000
            ),
        }
        normalized["case_hash"] = _canonical_hash(normalized)
        cases.append(normalized)
    cases.sort(key=lambda item: item["id"])
    payload = {
        "schema_version": LEARNING_VERSION,
        "registry_id": registry_id,
        "cases": cases,
        "promotion_policy": {
            "candidate_to_confirmed": "human or domain expert confirmation with reproducible evidence",
            "confirmed_to_retired": "human decision with replacement or invalidation evidence",
            "automatic_promotion": False,
        },
    }
    confirmed = [item for item in cases if item["status"] == "confirmed"]
    payload["must_kill_cases"] = [
        {
            "id": item["id"],
            "title": item["title"],
            "counterexample": item["counterexample"],
            "expected": item["expected"],
            "source_case_hash": item["case_hash"],
            "hidden_from_owner": item["hidden_from_owner"],
        }
        for item in confirmed
    ]
    payload["metrics"] = {
        "total": len(cases),
        "candidate": sum(item["status"] == "candidate" for item in cases),
        "confirmed": len(confirmed),
        "retired": sum(item["status"] == "retired" for item in cases),
    }
    return {**payload, "registry_hash": _canonical_hash(payload)}


def validate_bad_case_registry(registry: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(registry, Mapping):
        raise ValueError("bad case registry must be an object")
    expected = compile_bad_case_registry(
        {"registry_id": registry.get("registry_id"), "cases": registry.get("cases") or []}
    )
    if dict(registry) != expected:
        raise ValueError("bad case registry integrity check failed")
    return expected


def _ratio(numerator: int, denominator: int) -> float:
    return round(float(numerator) / denominator, 4) if denominator else 0.0


def compile_inspector_calibration(source: Mapping[str, Any]) -> Dict[str, Any]:
    """Score one Inspector against human-labelled Good/Bad Cases.

    A profile gains blocking authority only when every frozen threshold passes;
    otherwise it remains visible in shadow mode.
    """
    if not isinstance(source, Mapping):
        raise ValueError("inspector calibration source must be an object")
    lane_id = _text(source.get("lane_id"), "lane_id", required=True, limit=120)
    raw_policy = source.get("policy") or {}
    if not isinstance(raw_policy, Mapping):
        raise ValueError("calibration policy must be an object")
    policy = {
        "minimum_cases": int(raw_policy.get("minimum_cases", 8)),
        "minimum_positive_cases": int(raw_policy.get("minimum_positive_cases", 5)),
        "minimum_negative_cases": int(raw_policy.get("minimum_negative_cases", 3)),
        "minimum_recall": float(raw_policy.get("minimum_recall", 0.9)),
        "maximum_false_positive_rate": float(
            raw_policy.get("maximum_false_positive_rate", 0.1)
        ),
        "minimum_human_agreement": float(
            raw_policy.get("minimum_human_agreement", 0.9)
        ),
        "minimum_independent_contributions": int(
            raw_policy.get("minimum_independent_contributions", 1)
        ),
    }
    if any(policy[name] < 0 for name in policy):
        raise ValueError("calibration policy thresholds cannot be negative")
    for name in (
        "minimum_recall",
        "maximum_false_positive_rate",
        "minimum_human_agreement",
    ):
        if policy[name] > 1:
            raise ValueError("calibration rate thresholds must be between 0 and 1")
        if not math.isfinite(policy[name]):
            raise ValueError("calibration rate thresholds must be finite")

    evaluations = []
    seen = set()
    for raw in _objects(source.get("evaluations"), "evaluations"):
        case_id = _text(raw.get("case_id"), "calibration case_id", required=True, limit=120)
        if case_id in seen:
            raise ValueError("duplicate calibration case_id: %s" % case_id)
        seen.add(case_id)
        case_hash = _text(
            raw.get("case_hash"), "calibration case_hash", required=True, limit=64
        )
        if not HEX_64.match(case_hash):
            raise ValueError("calibration case_hash must be a sha256 digest")
        evaluations.append(
            {
                "case_id": case_id,
                "case_hash": case_hash,
                "expected_defect": bool(raw.get("expected_defect", False)),
                "reported_defect": bool(raw.get("reported_defect", False)),
                "human_agrees": bool(raw.get("human_agrees", False)),
                "independent_contribution": bool(
                    raw.get("independent_contribution", False)
                ),
                "labelled_by": _text(
                    raw.get("labelled_by"),
                    "calibration labelled_by",
                    required=True,
                    limit=200,
                ),
                "label_evidence": _text(
                    raw.get("label_evidence"),
                    "calibration label_evidence",
                    required=True,
                    limit=4000,
                ),
            }
        )
    positives = sum(item["expected_defect"] for item in evaluations)
    negatives = len(evaluations) - positives
    true_positives = sum(
        item["expected_defect"] and item["reported_defect"] for item in evaluations
    )
    false_positives = sum(
        not item["expected_defect"] and item["reported_defect"] for item in evaluations
    )
    agreements = sum(item["human_agrees"] for item in evaluations)
    contributions = sum(
        item["reported_defect"] and item["independent_contribution"]
        for item in evaluations
    )
    metrics = {
        "cases": len(evaluations),
        "positive_cases": positives,
        "negative_cases": negatives,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "bad_case_recall": _ratio(true_positives, positives),
        "false_positive_rate": _ratio(false_positives, negatives),
        "human_agreement_rate": _ratio(agreements, len(evaluations)),
        "independent_defect_contributions": contributions,
    }
    checks = {
        "enough_cases": metrics["cases"] >= policy["minimum_cases"],
        "enough_positive_cases": positives >= policy["minimum_positive_cases"],
        "enough_negative_cases": negatives >= policy["minimum_negative_cases"],
        "recall": metrics["bad_case_recall"] >= policy["minimum_recall"],
        "false_positive_rate": metrics["false_positive_rate"]
        <= policy["maximum_false_positive_rate"],
        "human_agreement": metrics["human_agreement_rate"]
        >= policy["minimum_human_agreement"],
        "independent_contribution": contributions
        >= policy["minimum_independent_contributions"],
    }
    mode = "blocking" if checks and all(checks.values()) else "shadow"
    payload = {
        "schema_version": LEARNING_VERSION,
        "lane_id": lane_id,
        "evaluations": evaluations,
        "policy": policy,
        "metrics": metrics,
        "checks": checks,
        "mode": mode,
        "promotion": {
            "automatic": False,
            "meaning": "blocking eligibility, never merge/release authority",
        },
    }
    return {**payload, "profile_hash": _canonical_hash(payload)}


def validate_inspector_calibration(profile: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise ValueError("inspector calibration profile must be an object")
    expected = compile_inspector_calibration(
        {
            "lane_id": profile.get("lane_id"),
            "evaluations": profile.get("evaluations") or [],
            "policy": profile.get("policy") or {},
        }
    )
    if dict(profile) != expected:
        raise ValueError("inspector calibration integrity check failed")
    return expected


def calibration_modes(profiles: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, str]]:
    result: Dict[str, Dict[str, str]] = {}
    for raw in list(profiles or [])[:100]:
        profile = validate_inspector_calibration(raw)
        lane_id = profile["lane_id"]
        if lane_id in result:
            raise ValueError("duplicate inspector calibration lane_id: %s" % lane_id)
        result[lane_id] = {
            "mode": profile["mode"],
            "profile_hash": profile["profile_hash"],
        }
    return result
