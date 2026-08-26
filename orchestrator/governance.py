"""Deterministic governance for AI-native software delivery.

This module deliberately does not run an agent, own task state, or call a model.
It compiles a human-owned work contract, selects independent verification lanes,
and adjudicates structured evidence.  LobeHub, Kandev, Symphony, Codex, or any
other runtime can consume the same policy without becoming a second source of
truth.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from .governance_learning import (
    calibration_modes,
    validate_bad_case_registry,
)


CONTRACT_VERSION = "2.3"
PLAN_VERSION = "2.3"
INTENT_VERSION = "2.3"
RESEARCH_VERSION = "2.3"
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
UNCERTAINTY_CATEGORIES = frozenset(
    {"policy_choice", "domain_fact", "engineering_invariant", "researchable_fact"}
)
UNCERTAINTY_STATUSES = frozenset(
    {
        "unresolved",
        "resolved",
        "expert_confirmed",
        "repository_proven",
        "researched",
        "known",
        "assumed",
        "unknown",
        "contested",
        "delegated",
        "blocked",
    }
)
UNCERTAINTY_IMPACTS = frozenset({"low", "medium", "high", "critical"})
DECISION_OWNERS = frozenset({"human", "domain_expert", "agent", "deterministic_rule"})
EVIDENCE_CLASSES = frozenset({"property_test", "mutation_test", "browser_e2e"})
RESEARCH_CHANNELS = frozenset(
    {"community", "academic", "open_source", "official"}
)
RESEARCH_SOURCE_KINDS = frozenset(
    {
        "forum_thread",
        "community_case_study",
        "peer_reviewed_paper",
        "preprint",
        "official_documentation",
        "repository",
        "standard",
        "benchmark",
    }
)
FRAMEWORK_SCORE_DIMENSIONS = (
    "requirements_fit",
    "maturity",
    "maintenance",
    "security",
    "integration_fit",
    "extensibility",
    "ecosystem",
    "license_fit",
)

LANE_PRESENTATION = {
    "deterministic-ci": ("确定性门禁", "运行编译、测试、扫描和仓库原生检查"),
    "contract-domain-semantics": ("需求语义审查", "反证需求符合性、领域语义和禁止结论"),
    "state-trust-boundaries": ("信任边界审查", "追踪状态、类型、身份、权限和审计真实性"),
    "test-oracle-falsification": ("测试反证审查", "用突变、属性和边界案例验证测试是否真能失败"),
    "security": ("安全攻防审查", "验证攻击输入、权限、凭据和可利用路径"),
    "data-compatibility": ("数据兼容审查", "验证数据归属、迁移、Schema 与 API 兼容"),
    "e2e-ux": ("端到端体验审查", "在真实浏览器中完成关键用户路径"),
    "reliability-cost": ("稳定性成本审查", "验证恢复、并发、资源与执行预算"),
    "adversarial-falsification": ("对抗破坏验证", "主动构造反例、绕过和冲突状态"),
}

ALLOWED_RISK_FLAGS = frozenset(
    {
        "architecture",
        "authentication",
        "authorization",
        "billing",
        "data_migration",
        "data_deletion",
        "external_write",
        "irreversible",
        "multi_tenant",
        "performance",
        "privacy",
        "production_release",
        "public_api",
        "schema_change",
        "secrets",
        "supply_chain",
        "user_experience",
    }
)

RISK_MARKERS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("irreversible", ("irreversible", "不可逆")),
    ("data_deletion", ("delete data", "data deletion", "删除数据", "清空数据")),
    ("production_release", ("production", "deploy", "release", "生产", "发布", "部署")),
    ("billing", ("billing", "payment", "invoice", "money", "计费", "支付", "账单", "金额")),
    ("authentication", ("authentication", "login", "oauth", "登录", "认证")),
    ("authorization", ("authorization", "permission", "rbac", "权限", "授权")),
    ("privacy", ("privacy", "personal data", "pii", "隐私", "个人信息")),
    ("secrets", ("secret", "credential", "token", "api key", "密钥", "凭据")),
    ("data_migration", ("migration", "migrate", "数据库迁移", "数据迁移")),
    ("multi_tenant", ("multi-tenant", "multitenant", "tenant", "多租户", "租户")),
    ("external_write", ("external write", "send email", "publish", "对外写", "发送邮件")),
    ("public_api", ("public api", "api contract", "公开 api", "接口兼容")),
    ("schema_change", ("schema", "database field", "表结构", "字段变更")),
    ("supply_chain", ("dependency", "package", "依赖", "供应链")),
    ("performance", ("performance", "latency", "throughput", "性能", "延迟", "吞吐")),
    ("user_experience", ("frontend", "user flow", "ui", "ux", "前端", "用户流程", "界面")),
    ("architecture", ("architecture", "refactor", "架构", "重构")),
)

CRITICAL_FLAGS = frozenset({"irreversible", "data_deletion", "production_release"})
HIGH_FLAGS = frozenset(
    {
        "authentication",
        "authorization",
        "billing",
        "data_migration",
        "external_write",
        "multi_tenant",
        "privacy",
        "secrets",
    }
)
MEDIUM_FLAGS = frozenset(
    {
        "architecture",
        "performance",
        "public_api",
        "schema_change",
        "supply_chain",
        "user_experience",
    }
)


def _strings(value: Any, field: str, limit: int = 100) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        raise ValueError("%s must be an array of strings" % field)
    if not isinstance(value, Iterable):
        raise ValueError("%s must be an array of strings" % field)
    result: List[str] = []
    seen = set()
    for raw in list(value)[:limit]:
        if not isinstance(raw, str):
            raise ValueError("%s entries must be strings" % field)
        item = " ".join(raw.strip().split())
        if not item:
            continue
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            result.append(item[:2000])
    return result


def _text(value: Any, field: str, required: bool = False, limit: int = 12000) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError("%s must be a string" % field)
    result = value.strip()[:limit]
    if required and not result:
        raise ValueError("%s is required" % field)
    return result


def _objects(value: Any, field: str, limit: int = 100) -> List[Mapping[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ValueError("%s must be an array of objects" % field)
    result: List[Mapping[str, Any]] = []
    for raw in list(value)[:limit]:
        if not isinstance(raw, Mapping):
            raise ValueError("%s entries must be objects" % field)
        result.append(raw)
    return result


def _environment(value: Any) -> Dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("environment must be an object")
    allowed = {"required_commands", "required_ports"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unknown environment fields: %s" % ", ".join(unknown))
    ports: List[int] = []
    for raw in list(value.get("required_ports") or [])[:50]:
        try:
            port = int(raw)
        except (TypeError, ValueError):
            raise ValueError("environment required_ports entries must be integers")
        if port < 1 or port > 65535:
            raise ValueError("environment required_ports entries must be between 1 and 65535")
        if port not in ports:
            ports.append(port)
    return {
        "required_commands": _strings(
            value.get("required_commands"), "environment.required_commands", limit=50
        ),
        "required_ports": ports,
    }


def _uncertainties(value: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(_objects(value, "uncertainties")):
        category = _text(raw.get("category"), "uncertainty category", required=True, limit=40)
        if category not in UNCERTAINTY_CATEGORIES:
            raise ValueError("unknown uncertainty category: %s" % category)
        status = _text(
            raw.get("state", raw.get("status")), "uncertainty state", limit=40
        ) or "unknown"
        if status not in UNCERTAINTY_STATUSES:
            raise ValueError("unknown uncertainty status: %s" % status)
        statement = _text(raw.get("statement"), "uncertainty statement", required=True, limit=2000)
        question = _text(raw.get("question"), "uncertainty question", limit=2000)
        item_id = _text(
            raw.get("decision_id", raw.get("id")), "uncertainty decision_id", limit=120
        ) or "uncertainty-%d" % (index + 1)
        if item_id in seen:
            raise ValueError("duplicate uncertainty id: %s" % item_id)
        seen.add(item_id)
        impact = _text(raw.get("impact"), "uncertainty impact", limit=20) or (
            "high" if category in {"policy_choice", "domain_fact"} else "medium"
        )
        if impact not in UNCERTAINTY_IMPACTS:
            raise ValueError("unknown uncertainty impact: %s" % impact)
        decision_owner = _text(
            raw.get("decision_owner", raw.get("owner")),
            "uncertainty decision_owner",
            limit=40,
        ) or {
            "policy_choice": "human",
            "domain_fact": "domain_expert",
            "engineering_invariant": "deterministic_rule",
            "researchable_fact": "agent",
        }[category]
        if decision_owner not in DECISION_OWNERS:
            raise ValueError("unknown uncertainty decision_owner: %s" % decision_owner)
        if status == "delegated" and decision_owner != "human":
            raise ValueError("delegated uncertainty requires an explicit human decision_owner")
        acceptance_ids = _strings(
            raw.get("acceptance_ids"), "uncertainty acceptance_ids", limit=50
        )
        acceptance_id = _text(raw.get("acceptance_id"), "uncertainty acceptance_id", limit=120)
        if acceptance_id and acceptance_id not in acceptance_ids:
            acceptance_ids.append(acceptance_id)
        reversible = bool(raw.get("reversible", False))
        unresolved_state = status in {
            "unresolved",
            "unknown",
            "contested",
            "delegated",
            "blocked",
        }
        blocking = (
            category in {"policy_choice", "domain_fact"}
            and (
                status == "blocked"
                or (
                    unresolved_state
                    and RISK_ORDER[impact] >= RISK_ORDER["high"]
                )
                or (
                    status == "assumed"
                    and not reversible
                    and RISK_ORDER[impact] >= RISK_ORDER["high"]
                )
            )
        )
        route = {
            "policy_choice": "ask_human",
            "domain_fact": "expert_review",
            "engineering_invariant": "prove_from_repository",
            "researchable_fact": "research_without_interrupting_owner",
        }[category]
        result.append(
            {
                "id": item_id,
                "decision_id": item_id,
                "category": category,
                "statement": statement,
                "question": question,
                "status": status,
                "state": status,
                "impact": impact,
                "acceptance_ids": acceptance_ids,
                "consequence": _text(
                    raw.get("consequence"), "uncertainty consequence", limit=4000
                ),
                "proposed_default": _text(
                    raw.get("proposed_default"),
                    "uncertainty proposed_default",
                    limit=2000,
                ),
                "decision_owner": decision_owner,
                "reversible": reversible,
                "answer": _text(raw.get("answer"), "uncertainty answer", limit=4000),
                "blocking": blocking,
                "route": route,
                "evidence": _text(raw.get("evidence"), "uncertainty evidence", limit=4000),
            }
        )
    return result


def _must_kill_cases(value: Any) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen = set()
    for index, raw in enumerate(_objects(value, "must_kill_cases", limit=50)):
        case_id = _text(raw.get("id"), "must-kill id", limit=120) or "must-kill-%d" % (index + 1)
        if case_id in seen:
            raise ValueError("duplicate must-kill id: %s" % case_id)
        seen.add(case_id)
        result.append(
            {
                "id": case_id,
                "title": _text(raw.get("title"), "must-kill title", required=True, limit=500),
                "counterexample": _text(
                    raw.get("counterexample"), "must-kill counterexample", required=True, limit=4000
                ),
                "expected": _text(raw.get("expected"), "must-kill expected", required=True, limit=2000),
            }
        )
    return result


def _intent_examples(value: Any) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen = set()
    for index, raw in enumerate(_objects(value, "acceptance_examples", limit=50)):
        example_id = _text(
            raw.get("id"), "acceptance example id", limit=120
        ) or "example-%d" % (index + 1)
        if example_id in seen:
            raise ValueError("duplicate acceptance example id: %s" % example_id)
        seen.add(example_id)
        result.append(
            {
                "id": example_id,
                "input": _text(
                    raw.get("input"), "acceptance example input", required=True, limit=4000
                ),
                "expected_output": _text(
                    raw.get("expected_output"),
                    "acceptance example expected_output",
                    required=True,
                    limit=4000,
                ),
                "notes": _text(raw.get("notes"), "acceptance example notes", limit=2000),
            }
        )
    return result


def _intent_choices(value: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(_objects(value, "technical_choices", limit=50)):
        choice_id = _text(raw.get("id"), "technical choice id", limit=120) or (
            "technical-choice-%d" % (index + 1)
        )
        if choice_id in seen:
            raise ValueError("duplicate technical choice id: %s" % choice_id)
        seen.add(choice_id)
        result.append(
            {
                "id": choice_id,
                "topic": _text(
                    raw.get("topic"), "technical choice topic", required=True, limit=500
                ),
                "selected": _text(
                    raw.get("selected"), "technical choice selected", limit=1000
                ),
                "alternatives": _strings(
                    raw.get("alternatives"), "technical choice alternatives", limit=20
                ),
                "rationale": _text(
                    raw.get("rationale"), "technical choice rationale", limit=4000
                ),
                "evidence": _text(
                    raw.get("evidence"), "technical choice evidence", limit=4000
                ),
                "research_hash": _text(
                    raw.get("research_hash"),
                    "technical choice research_hash",
                    limit=64,
                ),
                "high_impact": bool(raw.get("high_impact", True)),
            }
        )
    return result


def _intent_runtime(value: Any, field: str) -> Dict[str, str]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("%s must be an object" % field)
    return {
        "provider": _text(value.get("provider"), "%s provider" % field, limit=200),
        "model": _text(value.get("model"), "%s model" % field, limit=200),
        "authentication": _text(
            value.get("authentication"), "%s authentication" % field, limit=1000
        ),
        "purpose": _text(value.get("purpose"), "%s purpose" % field, limit=2000),
    }


def _intent_coverage(value: Any) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen = set()
    for raw in _objects(value, "coverage", limit=200):
        requirement_id = _text(
            raw.get("requirement_id"), "coverage requirement_id", required=True, limit=120
        )
        if requirement_id in seen:
            raise ValueError("duplicate coverage requirement_id: %s" % requirement_id)
        seen.add(requirement_id)
        status = _text(raw.get("status"), "coverage status", required=True, limit=40)
        if status not in {"covered", "missing", "changed", "unproven"}:
            raise ValueError("unknown coverage status: %s" % status)
        result.append(
            {
                "requirement_id": requirement_id,
                "status": status,
                "evidence": _text(raw.get("evidence"), "coverage evidence", limit=4000),
            }
        )
    return result


def _intent_findings(value: Any) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen = set()
    allowed_categories = {
        "goal_substitution",
        "requirement_omission",
        "provider_confusion",
        "unconfirmed_default",
        "unprovable_acceptance",
        "research_conflict",
        "other",
    }
    for index, raw in enumerate(_objects(value, "intent findings", limit=100)):
        finding_id = _text(raw.get("id"), "intent finding id", limit=120) or (
            "intent-finding-%d" % (index + 1)
        )
        if finding_id in seen:
            raise ValueError("duplicate intent finding id: %s" % finding_id)
        seen.add(finding_id)
        category = _text(
            raw.get("category"), "intent finding category", required=True, limit=80
        )
        if category not in allowed_categories:
            raise ValueError("unknown intent finding category: %s" % category)
        status = _text(raw.get("status"), "intent finding status", limit=40) or "blocking"
        if status not in {"blocking", "warning", "resolved"}:
            raise ValueError("unknown intent finding status: %s" % status)
        result.append(
            {
                "id": finding_id,
                "category": category,
                "status": status,
                "title": _text(
                    raw.get("title"), "intent finding title", required=True, limit=500
                ),
                "evidence": _text(
                    raw.get("evidence"), "intent finding evidence", required=True, limit=4000
                ),
                "question_for_human": _text(
                    raw.get("question_for_human"),
                    "intent finding question_for_human",
                    limit=2000,
                ),
            }
        )
    return result


def _iso_date(value: Any, field: str, required: bool = True) -> str:
    text = _text(value, field, required=required, limit=10)
    if not text:
        return ""
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise ValueError("%s must be an ISO date in YYYY-MM-DD form" % field)
    return parsed.isoformat()


def _research_queries(value: Any) -> Dict[str, List[str]]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("research queries must be an object")
    unknown = sorted(set(value) - RESEARCH_CHANNELS)
    if unknown:
        raise ValueError("unknown research query channels: %s" % ", ".join(unknown))
    return {
        channel: _strings(value.get(channel), "research queries.%s" % channel, limit=20)
        for channel in sorted(RESEARCH_CHANNELS)
    }


def _research_sources(value: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(_objects(value, "research sources", limit=200)):
        source_id = _text(raw.get("id"), "research source id", limit=120) or (
            "research-source-%d" % (index + 1)
        )
        if source_id in seen:
            raise ValueError("duplicate research source id: %s" % source_id)
        seen.add(source_id)
        channel = _text(
            raw.get("channel"), "research source channel", required=True, limit=40
        )
        if channel not in RESEARCH_CHANNELS:
            raise ValueError("unknown research source channel: %s" % channel)
        kind = _text(
            raw.get("kind"), "research source kind", required=True, limit=80
        )
        if kind not in RESEARCH_SOURCE_KINDS:
            raise ValueError("unknown research source kind: %s" % kind)
        url = _text(raw.get("url"), "research source url", required=True, limit=2000)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("research source url must be an absolute http(s) URL")
        result.append(
            {
                "id": source_id,
                "channel": channel,
                "kind": kind,
                "title": _text(
                    raw.get("title"), "research source title", required=True, limit=1000
                ),
                "url": url,
                "publisher": _text(
                    raw.get("publisher"),
                    "research source publisher",
                    required=True,
                    limit=500,
                ),
                "published_at": _iso_date(
                    raw.get("published_at"), "research source published_at"
                ),
                "accessed_at": _iso_date(
                    raw.get("accessed_at"), "research source accessed_at"
                ),
                "primary_evidence": bool(raw.get("primary_evidence", False)),
                "peer_reviewed": bool(raw.get("peer_reviewed", False)),
                "foundational": bool(raw.get("foundational", False)),
                "venue": _text(raw.get("venue"), "research source venue", limit=500),
                "identifier": _text(
                    raw.get("identifier"), "research source identifier", limit=500
                ),
                "methods_summary": _text(
                    raw.get("methods_summary"),
                    "research source methods_summary",
                    limit=3000,
                ),
                "summary": _text(
                    raw.get("summary"), "research source summary", required=True, limit=4000
                ),
                "claims": _strings(raw.get("claims"), "research source claims", limit=30),
                "quality_signals": _strings(
                    raw.get("quality_signals"),
                    "research source quality_signals",
                    limit=30,
                ),
                "limitations": _strings(
                    raw.get("limitations"), "research source limitations", limit=30
                ),
                "corroborates": _strings(
                    raw.get("corroborates"), "research source corroborates", limit=30
                ),
                "host": parsed.netloc.casefold().removeprefix("www."),
            }
        )
    return result


def _framework_candidates(value: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(_objects(value, "framework candidates", limit=30)):
        candidate_id = _text(raw.get("id"), "framework candidate id", limit=120) or (
            "framework-%d" % (index + 1)
        )
        if candidate_id in seen:
            raise ValueError("duplicate framework candidate id: %s" % candidate_id)
        seen.add(candidate_id)
        scores_raw = raw.get("scores") or {}
        if not isinstance(scores_raw, Mapping):
            raise ValueError("framework candidate scores must be an object")
        unknown_scores = sorted(set(scores_raw) - set(FRAMEWORK_SCORE_DIMENSIONS))
        if unknown_scores:
            raise ValueError("unknown framework score dimensions: %s" % ", ".join(unknown_scores))
        scores: Dict[str, Optional[int]] = {}
        for dimension in FRAMEWORK_SCORE_DIMENSIONS:
            raw_score = scores_raw.get(dimension)
            if raw_score is None:
                scores[dimension] = None
                continue
            try:
                score = int(raw_score)
            except (TypeError, ValueError):
                raise ValueError("framework scores must be integers from 0 to 5")
            if score < 0 or score > 5:
                raise ValueError("framework scores must be integers from 0 to 5")
            scores[dimension] = score
        repo_url = _text(
            raw.get("repository_url"),
            "framework repository_url",
            required=True,
            limit=2000,
        )
        docs_url = _text(
            raw.get("official_docs_url"),
            "framework official_docs_url",
            required=True,
            limit=2000,
        )
        for field, url in (("repository_url", repo_url), ("official_docs_url", docs_url)):
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("framework %s must be an absolute http(s) URL" % field)
        result.append(
            {
                "id": candidate_id,
                "name": _text(
                    raw.get("name"), "framework candidate name", required=True, limit=500
                ),
                "repository_url": repo_url,
                "official_docs_url": docs_url,
                "license": _text(
                    raw.get("license"), "framework license", required=True, limit=200
                ),
                "status": _text(
                    raw.get("status"), "framework status", required=True, limit=80
                ),
                "latest_release_at": _iso_date(
                    raw.get("latest_release_at"), "framework latest_release_at"
                ),
                "source_ids": _strings(
                    raw.get("source_ids"), "framework source_ids", limit=50
                ),
                "strengths": _strings(raw.get("strengths"), "framework strengths", limit=30),
                "gaps": _strings(raw.get("gaps"), "framework gaps", limit=30),
                "risks": _strings(raw.get("risks"), "framework risks", limit=30),
                "integration_notes": _text(
                    raw.get("integration_notes"),
                    "framework integration_notes",
                    required=True,
                    limit=4000,
                ),
                "scores": scores,
            }
        )
    return result


def _technology_paths(value: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(_objects(value, "technology paths", limit=10)):
        path_id = _text(raw.get("id"), "technology path id", limit=120) or (
            "path-%d" % (index + 1)
        )
        if path_id in seen:
            raise ValueError("duplicate technology path id: %s" % path_id)
        seen.add(path_id)
        result.append(
            {
                "id": path_id,
                "name": _text(
                    raw.get("name"), "technology path name", required=True, limit=500
                ),
                "approach": _text(
                    raw.get("approach"),
                    "technology path approach",
                    required=True,
                    limit=4000,
                ),
                "framework_ids": _strings(
                    raw.get("framework_ids"), "technology path framework_ids", limit=20
                ),
                "hypothesis": _text(
                    raw.get("hypothesis"),
                    "technology path hypothesis",
                    required=True,
                    limit=3000,
                ),
                "unknowns": _strings(
                    raw.get("unknowns"), "technology path unknowns", limit=30
                ),
                "strengths": _strings(
                    raw.get("strengths"), "technology path strengths", limit=30
                ),
                "risks": _strings(raw.get("risks"), "technology path risks", limit=30),
                "source_ids": _strings(
                    raw.get("source_ids"), "technology path source_ids", limit=50
                ),
                "estimated_effort": _text(
                    raw.get("estimated_effort"),
                    "technology path estimated_effort",
                    required=True,
                    limit=1000,
                ),
            }
        )
    return result


def _research_findings(value: Any) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen = set()
    categories = {
        "source_quality",
        "freshness",
        "academic_quality",
        "community_signal",
        "evidence_gap",
        "selection_bias",
        "claim_mismatch",
        "framework_fit",
        "license_security",
        "other",
    }
    for index, raw in enumerate(_objects(value, "research review findings", limit=100)):
        finding_id = _text(raw.get("id"), "research finding id", limit=120) or (
            "research-finding-%d" % (index + 1)
        )
        if finding_id in seen:
            raise ValueError("duplicate research finding id: %s" % finding_id)
        seen.add(finding_id)
        category = _text(
            raw.get("category"), "research finding category", required=True, limit=80
        )
        if category not in categories:
            raise ValueError("unknown research finding category: %s" % category)
        status = _text(raw.get("status"), "research finding status", limit=40) or "blocking"
        if status not in {"blocking", "warning", "resolved"}:
            raise ValueError("unknown research finding status: %s" % status)
        result.append(
            {
                "id": finding_id,
                "category": category,
                "status": status,
                "title": _text(
                    raw.get("title"), "research finding title", required=True, limit=500
                ),
                "evidence": _text(
                    raw.get("evidence"), "research finding evidence", required=True, limit=4000
                ),
                "required_action": _text(
                    raw.get("required_action"),
                    "research finding required_action",
                    limit=2000,
                ),
            }
        )
    return result


def _technology_strategy(value: Any) -> Dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("technology_strategy must be an object")
    mode = _text(value.get("mode"), "technology strategy mode", limit=40)
    if mode and mode not in {"single_path", "bounded_race"}:
        raise ValueError("technology strategy mode must be single_path or bounded_race")
    try:
        time_budget = int(value.get("time_budget_minutes", 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("technology strategy time_budget_minutes must be an integer")
    if time_budget < 0:
        raise ValueError("technology strategy time_budget_minutes cannot be negative")
    return {
        "mode": mode,
        "selected_path_ids": _strings(
            value.get("selected_path_ids"), "technology strategy selected_path_ids", limit=10
        ),
        "decision_rationale": _text(
            value.get("decision_rationale"),
            "technology strategy decision_rationale",
            limit=4000,
        ),
        "common_test_commands": _strings(
            value.get("common_test_commands"),
            "technology strategy common_test_commands",
            limit=30,
        ),
        "evaluation_dimensions": _strings(
            value.get("evaluation_dimensions"),
            "technology strategy evaluation_dimensions",
            limit=30,
        ),
        "time_budget_minutes": time_budget,
        "cost_budget": _text(
            value.get("cost_budget"), "technology strategy cost_budget", limit=1000
        ),
        "fusion_allowed": bool(value.get("fusion_allowed", False)),
        "stop_conditions": _strings(
            value.get("stop_conditions"), "technology strategy stop_conditions", limit=30
        ),
    }


def _intent_required(change_types: Sequence[str]) -> bool:
    normalized = {item.casefold() for item in change_types}
    low_only = {"documentation", "docs", "formatting", "style", "tests_only"}
    return not (normalized and normalized <= low_only)


def _intent_contract_view(source: Mapping[str, Any]) -> Dict[str, Any]:
    """Return only human-intent fields that an inspection must bind."""
    return {
        "goal": _text(source.get("goal"), "goal", required=True),
        "users": _strings(source.get("users"), "users"),
        "outcomes": _strings(source.get("outcomes"), "outcomes"),
        "acceptance_criteria": _strings(
            source.get("acceptance_criteria"), "acceptance_criteria"
        ),
        "non_goals": _strings(source.get("non_goals"), "non_goals"),
        "constraints": _strings(source.get("constraints"), "constraints"),
        "forbidden_behaviors": _strings(
            source.get("forbidden_behaviors"), "forbidden_behaviors"
        ),
        "human_decisions": _strings(
            source.get("human_decisions"), "human_decisions"
        ),
        "change_types": _strings(source.get("change_types"), "change_types"),
        "risk_flags": sorted(
            {
                item.casefold().replace("-", "_").replace(" ", "_")
                for item in _strings(source.get("risk_flags"), "risk_flags")
            }
        ),
    }


def _canonical_hash(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number > 1:
        number /= 100.0
    return max(0.0, min(1.0, number))


def _risk_level(
    flags: Sequence[str], change_types: Sequence[str], explicit_flags: Sequence[str]
) -> str:
    flag_set = set(flags)
    # A keyword is enough to widen inspection, but never enough by itself to
    # label an action irreversible/critical. Critical routing needs an explicit
    # declaration from the contract owner.
    if set(explicit_flags) & CRITICAL_FLAGS:
        return "critical"
    if flag_set & (CRITICAL_FLAGS | HIGH_FLAGS):
        return "high"
    if flag_set & MEDIUM_FLAGS:
        return "medium"
    low_only = {item.casefold() for item in change_types} <= {
        "documentation",
        "docs",
        "formatting",
        "style",
        "tests_only",
    }
    return "low" if change_types and low_only else "medium"


def _infer_flags(text: str) -> List[str]:
    lowered = text.casefold()
    return [flag for flag, markers in RISK_MARKERS if any(marker in lowered for marker in markers)]


@dataclass(frozen=True)
class LaneDefinition:
    lane_id: str
    kind: str
    purpose: str
    required_inputs: Tuple[str, ...]
    blocking_rule: str
    enabled_reason: str
    write_access: bool = False
    fresh_context: bool = True

    def to_dict(self) -> Dict[str, Any]:
        display_name, mission = LANE_PRESENTATION.get(
            self.lane_id, (self.lane_id, self.purpose)
        )
        return {
            "id": self.lane_id,
            "display_name": display_name,
            "mission": mission,
            "kind": self.kind,
            "purpose": self.purpose,
            "required_inputs": list(self.required_inputs),
            "blocking_rule": self.blocking_rule,
            "enabled_reason": self.enabled_reason,
            "write_access": self.write_access,
            "fresh_context": self.fresh_context,
            "developer_transcript_visible": False,
            "peer_findings_visible": False,
        }


class GovernanceEngine:
    """Compile contracts, route risk, and adjudicate evidence without side effects."""

    def compile_technology_research(self, source: Mapping[str, Any]) -> Dict[str, Any]:
        """Compile multi-channel technical research and an independent quality review.

        The caller performs network research.  This pure compiler makes source
        diversity, freshness, academic quality, framework comparison, viable
        paths, and reviewer independence fail-closed before intent confirmation.
        """
        if not isinstance(source, Mapping):
            raise ValueError("technology research input must be an object")
        research_question = _text(
            source.get("research_question"), "research_question", required=True
        )
        human_scope = _strings(source.get("human_scope"), "research human_scope")
        as_of = _iso_date(source.get("as_of"), "research as_of")
        as_of_date = date.fromisoformat(as_of)
        queries = _research_queries(source.get("queries"))
        sources = _research_sources(source.get("sources"))
        candidates = _framework_candidates(source.get("framework_candidates"))
        paths = _technology_paths(source.get("technology_paths"))
        recommendation_raw = source.get("recommendation") or {}
        if not isinstance(recommendation_raw, Mapping):
            raise ValueError("research recommendation must be an object")
        recommendation = {
            "selected_path_ids": _strings(
                recommendation_raw.get("selected_path_ids"),
                "research recommendation selected_path_ids",
                limit=10,
            ),
            "rationale": _text(
                recommendation_raw.get("rationale"),
                "research recommendation rationale",
                required=True,
                limit=6000,
            ),
            "source_ids": _strings(
                recommendation_raw.get("source_ids"),
                "research recommendation source_ids",
                limit=100,
            ),
            "key_tradeoffs": _strings(
                recommendation_raw.get("key_tradeoffs"),
                "research recommendation key_tradeoffs",
                limit=30,
            ),
            "rejected_alternatives": _strings(
                recommendation_raw.get("rejected_alternatives"),
                "research recommendation rejected_alternatives",
                limit=30,
            ),
            "confidence": _confidence(recommendation_raw.get("confidence")),
            "race_recommended": bool(
                recommendation_raw.get("race_recommended", False)
            ),
            "race_rationale": _text(
                recommendation_raw.get("race_rationale"),
                "research recommendation race_rationale",
                limit=4000,
            ),
        }
        declaration_raw = source.get("review_declaration") or {}
        if not isinstance(declaration_raw, Mapping):
            raise ValueError("research review_declaration must be an object")
        review_declaration = {
            "context_id": _text(
                declaration_raw.get("context_id"),
                "research review context_id",
                required=True,
                limit=200,
            ),
            "fresh_context": bool(declaration_raw.get("fresh_context", False)),
            "read_only": bool(declaration_raw.get("read_only", False)),
            "collector_transcript_visible": bool(
                declaration_raw.get("collector_transcript_visible", False)
            ),
            "candidate_implementation_visible": bool(
                declaration_raw.get("candidate_implementation_visible", False)
            ),
        }
        review_findings = _research_findings(source.get("review_findings"))
        requested_verdict = _text(
            source.get("review_verdict"),
            "research review_verdict",
            required=True,
            limit=20,
        ).upper()
        if requested_verdict not in {"PASS", "BLOCKED"}:
            raise ValueError("research review_verdict must be PASS or BLOCKED")

        blockers: List[Dict[str, str]] = []

        def block(item_id: str, category: str, title: str, evidence: str) -> None:
            blockers.append(
                {
                    "id": item_id,
                    "category": category,
                    "status": "blocking",
                    "title": title,
                    "evidence": evidence,
                    "required_action": "补充或修正研究证据后，由全新只读上下文重新质检。",
                }
            )

        if not human_scope:
            block(
                "human-scope",
                "evidence_gap",
                "研究没有绑定人确认的需求范围",
                "AI 可以扩展技术搜索，但不能自行改写产品目标、非目标和风险边界。",
            )
        for channel in sorted(RESEARCH_CHANNELS):
            if not queries[channel]:
                block(
                    "query-%s" % channel,
                    "evidence_gap",
                    "缺少 %s 调研查询" % channel,
                    "四路调研都必须留下实际使用的搜索问题。",
                )
        source_ids = {item["id"] for item in sources}
        by_channel = {
            channel: [item for item in sources if item["channel"] == channel]
            for channel in RESEARCH_CHANNELS
        }
        for item in sources:
            published = date.fromisoformat(item["published_at"])
            accessed = date.fromisoformat(item["accessed_at"])
            if published > as_of_date or accessed > as_of_date:
                block(
                    "future-%s" % item["id"],
                    "freshness",
                    "来源日期晚于研究截止日",
                    "%s 的日期不能由截止日 %s 观察到。" % (item["id"], as_of),
                )
            if not item["claims"] or not item["limitations"]:
                block(
                    "quality-%s" % item["id"],
                    "source_quality",
                    "来源缺少可用主张或局限说明",
                    "每个来源必须说明它支持什么，以及不能证明什么。",
                )

        community = by_channel["community"]
        community_hosts = {item["host"] for item in community}
        recent_community = [
            item
            for item in community
            if as_of_date.year - date.fromisoformat(item["published_at"]).year <= 3
        ]
        if len(community) < 2 or len(community_hosts) < 2 or not recent_community:
            block(
                "community-coverage",
                "community_signal",
                "社区实践证据不足或过于单一",
                "默认至少需要两个独立社区域名，其中至少一个近三年来源。",
            )

        academic = by_channel["academic"]
        recent_peer_reviewed = [
            item
            for item in academic
            if item["peer_reviewed"]
            and item["kind"] == "peer_reviewed_paper"
            and as_of_date.year - date.fromisoformat(item["published_at"]).year <= 5
        ]
        if len(academic) < 2 or not recent_peer_reviewed:
            block(
                "academic-coverage",
                "academic_quality",
                "近期高质量学术证据不足",
                "默认至少需要两项学术来源，且至少一项为近五年的同行评审研究。",
            )
        recent_academic_ids = {
            item["id"]
            for item in academic
            if as_of_date.year - date.fromisoformat(item["published_at"]).year <= 5
        }
        for item in academic:
            age = as_of_date.year - date.fromisoformat(item["published_at"]).year
            if not item["venue"] or not item["identifier"] or not item["methods_summary"]:
                block(
                    "academic-metadata-%s" % item["id"],
                    "academic_quality",
                    "学术来源缺少场所、标识符或方法信息",
                    "只凭标题或摘要不能证明研究质量和适用性。",
                )
            if age > 10 and (
                not item["foundational"]
                or not (set(item["corroborates"]) & recent_academic_ids)
            ):
                block(
                    "stale-academic-%s" % item["id"],
                    "freshness",
                    "陈旧论文没有基础性理由和近期佐证",
                    "十年以上论文不能成为当前技术选择的孤立依据。",
                )

        if len(by_channel["open_source"]) < 2 or len(by_channel["official"]) < 2:
            block(
                "open-source-coverage",
                "framework_fit",
                "开源框架或官方一手资料覆盖不足",
                "默认至少比较两个框架，并为候选提供官方仓库和官方文档证据。",
            )
        if len(candidates) < 2:
            block(
                "framework-candidates",
                "framework_fit",
                "没有形成至少两个可比较的开源框架候选",
                "不能只找到一个框架后直接宣布选择完成。",
            )
        candidate_ids = {item["id"] for item in candidates}
        for item in candidates:
            unknown_refs = sorted(set(item["source_ids"]) - source_ids)
            if unknown_refs:
                block(
                    "framework-refs-%s" % item["id"],
                    "claim_mismatch",
                    "框架评估引用了不存在的来源",
                    ", ".join(unknown_refs),
                )
            channels = {
                source["channel"]
                for source in sources
                if source["id"] in set(item["source_ids"])
            }
            if not {"open_source", "official"} <= channels:
                block(
                    "framework-primary-%s" % item["id"],
                    "source_quality",
                    "框架评估缺少仓库或官方资料",
                    "每个框架必须同时引用开源仓库证据和官方文档。",
                )
            if any(score is None for score in item["scores"].values()):
                block(
                    "framework-scores-%s" % item["id"],
                    "framework_fit",
                    "框架适配矩阵不完整",
                    "必须逐项评估需求适配、成熟度、维护、安全、集成、扩展、生态和许可证。",
                )
            if not item["strengths"] or not item["gaps"] or not item["risks"]:
                block(
                    "framework-balance-%s" % item["id"],
                    "selection_bias",
                    "框架评估缺少优点、缺口或风险",
                    "客观比较不能只有卖点或总分。",
                )

        if len(paths) < 2:
            block(
                "technology-paths",
                "evidence_gap",
                "没有形成至少两条可行技术路径",
                "研究必须展示真实替代方案，而不是只论证预设结论。",
            )
        path_ids = {item["id"] for item in paths}
        for item in paths:
            missing_frameworks = sorted(set(item["framework_ids"]) - candidate_ids)
            missing_sources = sorted(set(item["source_ids"]) - source_ids)
            if missing_frameworks or missing_sources:
                block(
                    "path-refs-%s" % item["id"],
                    "claim_mismatch",
                    "技术路径引用不完整",
                    "unknown frameworks=%s; unknown sources=%s"
                    % (missing_frameworks, missing_sources),
                )
            if not item["unknowns"]:
                block(
                    "path-unknowns-%s" % item["id"],
                    "selection_bias",
                    "技术路径没有陈述未知性",
                    "赛马价值来自无法仅靠文档消除的未知，而不是多开 Agent。",
                )
        if not recommendation["selected_path_ids"] or not set(
            recommendation["selected_path_ids"]
        ) <= path_ids:
            block(
                "recommendation-paths",
                "claim_mismatch",
                "研究推荐没有绑定有效技术路径",
                "推荐必须指向已评估的 path id。",
            )
        if not recommendation["source_ids"] or not set(
            recommendation["source_ids"]
        ) <= source_ids:
            block(
                "recommendation-sources",
                "evidence_gap",
                "研究推荐缺少有效来源链",
                "推荐理由必须可追溯到已记录来源。",
            )
        if not recommendation["key_tradeoffs"] or not recommendation[
            "rejected_alternatives"
        ]:
            block(
                "recommendation-balance",
                "selection_bias",
                "推荐缺少权衡或淘汰理由",
                "AI 应给出客观比较，不只给一个看似确定的答案。",
            )
        if recommendation["race_recommended"] and (
            len(recommendation["selected_path_ids"]) not in {2, 3}
            or not recommendation["race_rationale"]
        ):
            block(
                "race-recommendation",
                "evidence_gap",
                "赛马建议缺少 2-3 条路径或真实未知性理由",
                "只有多条路径均可行且文档不足以判定时才建议有界赛马。",
            )

        if not review_declaration["fresh_context"] or not review_declaration["read_only"]:
            block(
                "research-review-isolation",
                "source_quality",
                "调研质检不是全新只读上下文",
                "收集者不能同时作为自己的唯一质量证明。",
            )
        if review_declaration["collector_transcript_visible"] or review_declaration[
            "candidate_implementation_visible"
        ]:
            block(
                "research-review-leakage",
                "selection_bias",
                "调研质检看到了收集过程或候选实现",
                "质检应基于冻结证据包，不能被过程辩护或实现结果反向污染。",
            )
        blockers.extend(item for item in review_findings if item["status"] == "blocking")
        final_verdict = (
            "PASS" if requested_verdict == "PASS" and not blockers else "BLOCKED"
        )
        if requested_verdict == "BLOCKED" and not blockers:
            block(
                "review-blocked-without-finding",
                "other",
                "调研质检判定阻塞但没有提交原因",
                "阻塞必须附具体来源或比较证据。",
            )
            final_verdict = "BLOCKED"

        immutable = {
            "research_question": research_question,
            "human_scope": human_scope,
            "as_of": as_of,
            "queries": queries,
            "sources": sources,
            "framework_candidates": candidates,
            "technology_paths": paths,
            "recommendation": recommendation,
            "review_declaration": review_declaration,
            "review_findings": review_findings,
            "requested_verdict": requested_verdict,
            "verdict": final_verdict,
            "blockers": blockers,
        }
        research_hash = _canonical_hash(immutable)
        return {
            "schema_version": RESEARCH_VERSION,
            "artifact": "technology_research",
            "research_hash": research_hash,
            "status": "pass" if final_verdict == "PASS" else "blocked",
            "roles": [
                {
                    "id": "technology-researcher",
                    "display_name": "技术调研员",
                    "mission": "检索社区实践、近期学术证据、开源框架与官方资料，提出多条有证据的可行路径",
                    "may_change_human_scope": False,
                },
                {
                    "id": "research-quality-inspector",
                    "display_name": "调研质检员",
                    "mission": "在全新只读上下文检查来源质量、时效、适配矩阵、选择偏差和赛马必要性",
                    "may_edit_research": False,
                    "may_self_approve": False,
                },
            ],
            **immutable,
            "human_questions": [
                item["required_action"] or item["title"] for item in blockers
            ],
        }

    def compile_intent_brief(self, source: Mapping[str, Any]) -> Dict[str, Any]:
        """Compile the human-facing intent confirmation brief.

        This operation is deliberately pure.  It prepares the exact choices a
        human must see, but it cannot claim that a human approved them.
        """
        if not isinstance(source, Mapping):
            raise ValueError("intent brief input must be an object")
        original_request = _text(
            source.get("original_request"), "original_request", required=True
        )
        expected_outcomes = _strings(
            source.get("expected_outcomes"), "expected_outcomes"
        )
        acceptance_examples = _intent_examples(source.get("acceptance_examples"))
        development_executor = _intent_runtime(
            source.get("development_executor"), "development_executor"
        )
        product_runtime = _intent_runtime(
            source.get("product_runtime"), "product_runtime"
        )
        technical_choices = _intent_choices(source.get("technical_choices"))
        technology_research_raw = source.get("technology_research") or {}
        if technology_research_raw and not isinstance(technology_research_raw, Mapping):
            raise ValueError("technology_research must be an object")
        technology_research = (
            copy.deepcopy(dict(technology_research_raw))
            if technology_research_raw
            else {}
        )
        if technology_research:
            self._validate_technology_research(technology_research)
        technology_strategy = _technology_strategy(source.get("technology_strategy"))
        non_goals = _strings(source.get("non_goals"), "intent non_goals")
        risk_boundaries = _strings(
            source.get("risk_boundaries"), "intent risk_boundaries"
        )
        conversation_refs = _strings(
            source.get("conversation_refs"), "conversation_refs", limit=50
        )
        research_refs = _strings(source.get("research_refs"), "research_refs", limit=50)
        unresolved_questions = _strings(
            source.get("unresolved_questions"), "unresolved_questions", limit=50
        )

        questions: List[Dict[str, str]] = []

        def require(item_id: str, question: str, reason: str) -> None:
            questions.append({"id": item_id, "question": question, "reason": reason})

        if not technology_research:
            require(
                "technology_research",
                "请先完成社区、近期学术、开源框架和官方一手资料四路技术调研，并由全新只读质检员复核。",
                "人在确认技术路线前应看到有质量、时效和适配性证据的替代方案。",
            )
        elif technology_research.get("status") != "pass":
            require(
                "technology_research_blocked",
                "请先处理技术调研质检的阻塞项：%s"
                % "；".join(
                    technology_research.get("human_questions") or ["调研未通过"]
                ),
                "未经独立质检通过的搜索结果不能支撑高影响技术选择。",
            )

        research_hash = str(technology_research.get("research_hash") or "")
        research_paths = {
            str(item.get("id")): item
            for item in technology_research.get("technology_paths") or []
            if isinstance(item, Mapping)
        }
        selected_path_ids = technology_strategy["selected_path_ids"]
        missing_paths = sorted(set(selected_path_ids) - set(research_paths))
        if not technology_strategy["mode"]:
            require(
                "technology_strategy",
                "请确认采用单一路线，还是对 2–3 条存在真实未知性的路线进行有界赛马。",
                "AI 可以提出技术候选和赛马建议，但并行数量、预算和最终路线由人决定。",
            )
        elif missing_paths:
            require(
                "technology_strategy_paths",
                "所选技术路径不在已质检调研中，请重新选择：%s" % ", ".join(missing_paths),
                "技术决策必须能追溯到冻结的调研证据。",
            )
        elif technology_strategy["mode"] == "single_path":
            if len(selected_path_ids) != 1:
                require(
                    "single_path_selection",
                    "单一路线模式必须且只能选择一条已调研路径。",
                    "避免未确认的并行开发或模糊的最终技术方向。",
                )
            if not technology_strategy["decision_rationale"]:
                require(
                    "single_path_rationale",
                    "请确认为什么现在可以直接保留这条技术路线。",
                    "人的选择可以不同于 AI 推荐，但必须留下可审计理由。",
                )
        elif technology_strategy["mode"] == "bounded_race":
            if len(selected_path_ids) not in {2, 3}:
                require(
                    "race_path_count",
                    "有界技术赛马必须选择 2–3 条已调研路径。",
                    "少于两条无法比较，多于三条会把不确定性管理退化为无界消耗。",
                )
            if selected_path_ids and not all(
                (research_paths.get(path_id) or {}).get("unknowns")
                for path_id in selected_path_ids
            ):
                require(
                    "race_unknowns",
                    "请说明每条赛道有哪些无法仅靠文档消除、必须用原型和统一测试验证的未知性。",
                    "没有真实未知性时应直接选路，不应为了多 Agent 而赛马。",
                )
            recommendation = technology_research.get("recommendation") or {}
            if not recommendation.get("race_recommended"):
                require(
                    "race_necessity",
                    "调研没有证明赛马必要性；请补充真实未知性证据或改为单一路线。",
                    "并行实现的额外成本必须由证据而不是偏好来授权。",
                )
            for field, question, reason in (
                (
                    "common_test_commands",
                    "请冻结所有赛道共同使用的测试命令。",
                    "不同测试会让比赛失去可比性。",
                ),
                (
                    "evaluation_dimensions",
                    "请确认统一评测维度，例如质量、性能、成本、风险和集成难度。",
                    "先定标尺才能防止看到结果后改规则。",
                ),
                (
                    "stop_conditions",
                    "请确认赛马停止条件和全部失败时的处理。",
                    "有界实验必须能按预算停止，不能自动扩大消耗。",
                ),
            ):
                if not technology_strategy[field]:
                    require(field, question, reason)
            if technology_strategy["time_budget_minutes"] <= 0:
                require(
                    "race_time_budget",
                    "请确认每次技术赛马的总时间预算。",
                    "赛马必须有明确资源上限。",
                )
            if not technology_strategy["cost_budget"]:
                require(
                    "race_cost_budget",
                    "请确认技术赛马的成本预算或明确零外部付费。",
                    "并行路线不能自行购买额度或扩大外部成本。",
                )
            if not technology_strategy["decision_rationale"]:
                require(
                    "race_decision_rationale",
                    "请确认为什么本次值得开赛马，以及是否允许赛后融合明确优点。",
                    "是否赛马和是否融合都属于人的高影响选择。",
                )

        if not expected_outcomes:
            require(
                "expected_outcomes",
                "最终必须交付哪些用户可观察结果？",
                "不能只确认要开发什么，还要确认最终得到什么。",
            )
        if not acceptance_examples:
            require(
                "acceptance_examples",
                "请给出至少一个具体输入和预期输出的验收样例。",
                "没有样例时，金额、格式和失败行为等关键预期容易被实现者自行改写。",
            )
        for field, runtime, label in (
            ("development_executor", development_executor, "开发执行器"),
            ("product_runtime", product_runtime, "产品运行时"),
        ):
            if not runtime["provider"]:
                require(
                    "%s_provider" % field,
                    "%s使用哪个供应商或明确声明不适用？" % label,
                    "开发 Agent 与产品 Demo 的运行时是两个不同选择，不能混用。",
                )
            if not runtime["purpose"]:
                require(
                    "%s_purpose" % field,
                    "%s具体负责哪一部分？" % label,
                    "明确职责才能防止把开发登录方式误当成产品模型 API。",
                )
            provider = runtime["provider"].casefold()
            if provider not in {"", "none", "not_applicable", "not applicable"}:
                if not runtime["model"]:
                    require(
                        "%s_model" % field,
                        "%s使用哪个模型或明确的本机配置？" % label,
                        "供应商相同不代表模型和行为相同。",
                    )
                if not runtime["authentication"]:
                    require(
                        "%s_authentication" % field,
                        "%s的认证方式和密钥边界是什么？" % label,
                        "必须确认是本机登录还是产品环境变量，且密钥不能进入治理上下文。",
                    )
        for choice in technical_choices:
            if choice["high_impact"] and (not choice["selected"] or not choice["rationale"]):
                require(
                    choice["id"],
                    "请确认技术选择“%s”及其选择理由。" % choice["topic"],
                    "高影响技术选择不能由开发 Agent 静默默认。",
                )
            if choice["high_impact"] and (
                not research_hash or choice["research_hash"] != research_hash
            ):
                require(
                    "%s_research" % choice["id"],
                    "请让高影响技术选择“%s”绑定当前已质检 research_hash。"
                    % choice["topic"],
                    "选择理由必须能追溯到开发前的技术调研，不能只写结论。",
                )
        if not non_goals:
            require(
                "intent_non_goals",
                "本次明确不做哪些相邻能力？",
                "非目标用于防止范围扩张或用缩减范围掩盖偏差。",
            )
        if not risk_boundaries:
            require(
                "risk_boundaries",
                "哪些外部动作、成本、数据或法律结论必须停下来由人决定？",
                "风险边界决定自动化何时必须停止。",
            )
        for index, question in enumerate(unresolved_questions):
            require(
                "source-question-%d" % (index + 1),
                question,
                "原始需求仍将该事项标为未确认。",
            )

        immutable = {
            "original_request": original_request,
            "conversation_refs": conversation_refs,
            "expected_outcomes": expected_outcomes,
            "acceptance_examples": acceptance_examples,
            "development_executor": development_executor,
            "product_runtime": product_runtime,
            "technical_choices": technical_choices,
            "technology_research": technology_research,
            "research_hash": research_hash,
            "technology_strategy": technology_strategy,
            "technology_strategy_hash": _canonical_hash(technology_strategy),
            "non_goals": non_goals,
            "risk_boundaries": risk_boundaries,
            "research_refs": research_refs,
            "unresolved_questions": unresolved_questions,
        }
        intent_hash = _canonical_hash(immutable)
        return {
            "schema_version": INTENT_VERSION,
            "artifact": "intent_brief",
            "intent_hash": intent_hash,
            "status": "needs_clarification" if questions else "ready_for_inspection",
            "role": {
                "id": "intent-confirmer",
                "display_name": "意图确认员",
                "mission": "向人确认最终结果、技术边界、调研结论、赛马预算、运行时、验收样例与风险边界",
                "may_answer_for_human": False,
            },
            **immutable,
            "confirmation_questions": questions,
            "human_attestation_required": True,
        }

    def compile_intent_inspection(self, source: Mapping[str, Any]) -> Dict[str, Any]:
        """Compile a fresh-context, read-only semantic inspection result."""
        if not isinstance(source, Mapping):
            raise ValueError("intent inspection input must be an object")
        brief = source.get("brief")
        if not isinstance(brief, Mapping):
            raise ValueError("intent inspection brief must be an object")
        self._validate_intent_brief(brief)
        proposed_contract_source = source.get("proposed_contract_source")
        if not isinstance(proposed_contract_source, Mapping):
            raise ValueError("proposed_contract_source must be an object")
        technology_research = source.get("technology_research") or {}
        if technology_research and not isinstance(technology_research, Mapping):
            raise ValueError("intent inspection technology_research must be an object")
        if technology_research:
            self._validate_technology_research(technology_research)
        declaration = source.get("inspector_declaration") or {}
        if not isinstance(declaration, Mapping):
            raise ValueError("inspector_declaration must be an object")
        inspector_declaration = {
            "context_id": _text(
                declaration.get("context_id"), "inspector context_id", required=True, limit=200
            ),
            "fresh_context": bool(declaration.get("fresh_context", False)),
            "read_only": bool(declaration.get("read_only", False)),
            "owner_transcript_visible": bool(
                declaration.get("owner_transcript_visible", False)
            ),
            "peer_findings_visible": bool(
                declaration.get("peer_findings_visible", False)
            ),
        }
        evidence_inputs = _strings(
            source.get("evidence_inputs"), "intent inspection evidence_inputs", limit=20
        )
        research_evidence = _strings(
            source.get("research_evidence"), "intent inspection research_evidence", limit=100
        )
        coverage = _intent_coverage(source.get("coverage"))
        findings = _intent_findings(source.get("findings"))
        requested_verdict = _text(
            source.get("verdict"), "intent inspection verdict", required=True, limit=20
        ).upper()
        if requested_verdict not in {"PASS", "BLOCKED"}:
            raise ValueError("intent inspection verdict must be PASS or BLOCKED")

        blockers: List[Dict[str, str]] = []

        def block(item_id: str, category: str, title: str, evidence: str) -> None:
            blockers.append(
                {
                    "id": item_id,
                    "category": category,
                    "status": "blocking",
                    "title": title,
                    "evidence": evidence,
                    "question_for_human": "",
                }
            )

        if brief.get("status") != "ready_for_inspection":
            block(
                "brief-not-ready",
                "requirement_omission",
                "意图简报仍有未确认问题",
                "意图确认员必须先补齐简报，检查员不能代替人回答。",
            )
        if not technology_research:
            block(
                "research-artifact-missing",
                "research_conflict",
                "意图检查没有收到冻结的技术调研产物",
                "检查员必须直接比对 research_hash，不能只依赖拟定者的文字摘要。",
            )
        elif technology_research.get("status") != "pass":
            block(
                "research-artifact-blocked",
                "research_conflict",
                "技术调研质检未通过",
                "意图检查不能为被调研质检阻塞的技术选择放行。",
            )
        elif technology_research.get("research_hash") != brief.get("research_hash"):
            block(
                "research-hash-drift",
                "research_conflict",
                "意图简报绑定了不同的调研版本",
                "拟定技术选择与检查员看到的冻结 research_hash 不一致。",
            )
        if not inspector_declaration["fresh_context"] or not inspector_declaration["read_only"]:
            block(
                "inspection-isolation",
                "other",
                "意图检查未在全新只读上下文执行",
                "检查员必须独立于拟定者，且不能修改契约或产品代码。",
            )
        if inspector_declaration["owner_transcript_visible"] or inspector_declaration[
            "peer_findings_visible"
        ]:
            block(
                "inspection-leakage",
                "other",
                "意图检查上下文泄漏了执行者或同伴结论",
                "独立反证必须在提交前看不到这些材料。",
            )
        required_inputs = {
            "original_request",
            "intent_brief",
            "technical_research",
            "proposed_contract",
            "acceptance_examples",
        }
        missing_inputs = sorted(required_inputs - set(evidence_inputs))
        if missing_inputs:
            block(
                "inspection-inputs",
                "requirement_omission",
                "意图检查缺少必需的一手输入",
                "缺少：%s" % ", ".join(missing_inputs),
            )

        required_coverage = {
            "outcome-%d" % (index + 1)
            for index, _item in enumerate(brief.get("expected_outcomes") or [])
        }
        required_coverage.update(
            str(item.get("id")) for item in brief.get("acceptance_examples") or []
        )
        required_coverage.update(
            str(item.get("id")) for item in brief.get("technical_choices") or []
        )
        required_coverage.update(
            {
                "development-executor",
                "product-runtime",
                "research-recommendation",
                "technology-strategy",
            }
        )
        coverage_by_id = {item["requirement_id"]: item for item in coverage}
        for requirement_id in sorted(required_coverage):
            item = coverage_by_id.get(requirement_id)
            if item is None:
                block(
                    "coverage-%s" % requirement_id,
                    "requirement_omission",
                    "拟定契约没有证明覆盖意图项 %s" % requirement_id,
                    "检查结果必须逐项给出覆盖证据。",
                )
            elif item["status"] != "covered" or not item["evidence"]:
                block(
                    "coverage-%s" % requirement_id,
                    "goal_substitution" if item["status"] == "changed" else "requirement_omission",
                    "意图项 %s 未被原样覆盖" % requirement_id,
                    item["evidence"] or "没有覆盖证据。",
                )
        blockers.extend(item for item in findings if item["status"] == "blocking")
        final_verdict = (
            "PASS"
            if requested_verdict == "PASS" and not blockers
            else "BLOCKED"
        )
        if requested_verdict == "BLOCKED" and not blockers:
            block(
                "inspector-blocked-without-finding",
                "other",
                "检查员判定阻塞但未提交原因",
                "阻塞结论必须附具体证据。",
            )
            final_verdict = "BLOCKED"

        immutable = {
            "intent_hash": brief["intent_hash"],
            "brief": copy.deepcopy(dict(brief)),
            "proposed_contract_source": copy.deepcopy(dict(proposed_contract_source)),
            "proposed_contract_source_hash": _canonical_hash(proposed_contract_source),
            "technology_research": copy.deepcopy(dict(technology_research)),
            "research_hash": str(technology_research.get("research_hash") or ""),
            "research_evidence": research_evidence,
            "evidence_inputs": evidence_inputs,
            "inspector_declaration": inspector_declaration,
            "coverage": coverage,
            "findings": findings,
            "requested_verdict": requested_verdict,
            "verdict": final_verdict,
            "blockers": blockers,
        }
        inspection_hash = _canonical_hash(immutable)
        human_questions = [
            item["question_for_human"] or item["title"]
            for item in blockers
        ]
        return {
            "schema_version": INTENT_VERSION,
            "artifact": "intent_inspection",
            "inspection_hash": inspection_hash,
            "status": "pass" if final_verdict == "PASS" else "blocked",
            "role": {
                "id": "intent-inspector",
                "display_name": "意图检查员",
                "mission": "用全新只读上下文反查目标偷换、遗漏、供应商混淆、未确认默认和不可证明验收",
                "may_edit": False,
                "may_self_approve": False,
            },
            **immutable,
            "human_questions": human_questions,
        }

    def _compile_intent_alignment(
        self, value: Any, required: bool
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        if not required:
            return (
                {
                    "schema_version": INTENT_VERSION,
                    "required": False,
                    "status": "policy_exempt",
                    "exemption_reason": "Only low-risk documentation, formatting, style, or tests-only work is declared.",
                    "brief": {},
                    "inspection": {},
                    "intent_hash": "",
                    "inspection_hash": "",
                    "research_hash": "",
                    "technology_strategy_hash": "",
                    "technology_strategy": {},
                    "human_attestation_required": False,
                },
                [],
            )
        if value is None:
            value = {}
        if not isinstance(value, Mapping):
            raise ValueError("intent_alignment must be an object")
        brief = value.get("brief") or {}
        inspection = value.get("inspection") or {}
        clarifications: List[Dict[str, Any]] = []
        if not brief:
            clarifications.append(
                {
                    "id": "intent_brief",
                    "category": "policy_choice",
                    "question": "请先由意图确认员整理最终结果、技术选型、运行时、验收样例和风险边界。",
                    "reason": "非低风险开发不能直接从原始目标跳到实现契约。",
                }
            )
        else:
            if not isinstance(brief, Mapping):
                raise ValueError("intent_alignment.brief must be an object")
            self._validate_intent_brief(brief)
            if brief.get("status") != "ready_for_inspection":
                clarifications.extend(
                    {
                        "id": "intent-%s" % str(item.get("id")),
                        "category": "policy_choice",
                        "question": str(item.get("question") or "请补齐意图简报"),
                        "reason": str(item.get("reason") or "意图简报未就绪"),
                    }
                    for item in brief.get("confirmation_questions") or []
                )
        if not inspection:
            clarifications.append(
                {
                    "id": "intent_inspection",
                    "category": "policy_choice",
                    "question": "请让全新只读上下文的意图检查员独立复核原始对话、调研、拟定契约和验收样例。",
                    "reason": "拟定者不能同时证明自己没有偷换目标或遗漏需求。",
                }
            )
        else:
            if not isinstance(inspection, Mapping):
                raise ValueError("intent_alignment.inspection must be an object")
            self._validate_intent_inspection(inspection)
            if brief and inspection.get("intent_hash") != brief.get("intent_hash"):
                raise ValueError("intent inspection does not belong to the intent brief")
            if brief and inspection.get("research_hash") != brief.get("research_hash"):
                raise ValueError("intent inspection does not belong to the technology research")
            if inspection.get("status") != "pass":
                clarifications.append(
                    {
                        "id": "intent_inspection_blocked",
                        "category": "policy_choice",
                        "question": "请处理意图检查员提出的阻塞项：%s"
                        % "；".join(inspection.get("human_questions") or ["检查未通过"]),
                        "reason": "意图检查员发现目标偷换、遗漏、默认选择或验收不可证明。",
                    }
                )
        passed = bool(brief and inspection and not clarifications)
        return (
            {
                "schema_version": INTENT_VERSION,
                "required": True,
                "status": "pass_pending_human_attestation" if passed else "blocked",
                "exemption_reason": "",
                "brief": copy.deepcopy(dict(brief)),
                "inspection": copy.deepcopy(dict(inspection)),
                "intent_hash": str(brief.get("intent_hash") or ""),
                "inspection_hash": str(inspection.get("inspection_hash") or ""),
                "research_hash": str(brief.get("research_hash") or ""),
                "technology_strategy_hash": str(
                    brief.get("technology_strategy_hash") or ""
                ),
                "technology_strategy": copy.deepcopy(
                    brief.get("technology_strategy") or {}
                ),
                "human_attestation_required": True,
            },
            clarifications,
        )

    def compile_contract(self, source: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(source, Mapping):
            raise ValueError("contract input must be an object")
        goal = _text(source.get("goal"), "goal", required=True)
        title = _text(source.get("title"), "title") or goal.splitlines()[0][:120]
        users = _strings(source.get("users"), "users")
        outcomes = _strings(source.get("outcomes"), "outcomes")
        acceptance = _strings(source.get("acceptance_criteria"), "acceptance_criteria")
        non_goals = _strings(source.get("non_goals"), "non_goals")
        constraints = _strings(source.get("constraints"), "constraints")
        forbidden = _strings(source.get("forbidden_behaviors"), "forbidden_behaviors")
        human_decisions = _strings(source.get("human_decisions"), "human_decisions")
        deterministic_checks = _strings(
            source.get("deterministic_checks"), "deterministic_checks"
        )
        change_types = _strings(source.get("change_types"), "change_types")
        intent_alignment, intent_clarifications = self._compile_intent_alignment(
            source.get("intent_alignment"), _intent_required(change_types)
        )
        if intent_alignment.get("required") and intent_alignment.get("inspection"):
            inspected_source = intent_alignment["inspection"].get(
                "proposed_contract_source"
            ) or {}
            if _intent_contract_view(inspected_source) != _intent_contract_view(source):
                intent_clarifications.append(
                    {
                        "id": "intent_contract_drift",
                        "category": "policy_choice",
                        "question": "拟定契约在意图检查后发生了变化，请用全新只读上下文重新检查当前版本。",
                        "reason": "旧 inspection_hash 不能证明修改后的目标、验收或技术边界仍忠于原始意图。",
                    }
                )
                intent_alignment["status"] = "blocked"
        environment = _environment(source.get("environment"))
        uncertainty_registry = _uncertainties(source.get("uncertainties"))
        must_kill_cases = _must_kill_cases(source.get("must_kill_cases"))
        required_evidence_classes = sorted(
            {
                item.casefold().replace("-", "_").replace(" ", "_")
                for item in _strings(
                    source.get("required_evidence_classes"),
                    "required_evidence_classes",
                    limit=20,
                )
            }
        )
        unknown_evidence = sorted(set(required_evidence_classes) - EVIDENCE_CLASSES)
        if unknown_evidence:
            raise ValueError(
                "unknown required_evidence_classes: %s" % ", ".join(unknown_evidence)
            )
        explicit_flags = sorted(
            {
                item.casefold().replace("-", "_").replace(" ", "_")
                for item in _strings(source.get("risk_flags"), "risk_flags")
            }
        )
        unknown_flags = sorted(set(explicit_flags) - ALLOWED_RISK_FLAGS)
        if unknown_flags:
            raise ValueError("unknown risk_flags: %s" % ", ".join(unknown_flags))

        # Infer risk only from the work being requested. Constraints and
        # forbidden behaviours describe guardrails, so sentences such as
        # "do not deploy" or "do not add an API key" must not manufacture
        # production-release or secrets risk that the change itself does not
        # touch.
        combined_text = "\n".join([goal] + outcomes + acceptance + change_types)
        inferred = _infer_flags(combined_text)
        risk_flags = sorted(set(explicit_flags) | set(inferred))
        level = _risk_level(risk_flags, change_types, explicit_flags)

        clarifications: List[Dict[str, Any]] = list(intent_clarifications)
        if not users:
            clarifications.append(
                {
                    "id": "target_users",
                    "category": "policy_choice",
                    "question": "谁会在什么场景下使用这项交付？",
                    "reason": "没有用户场景时，功能测试容易退化为实现自证。",
                }
            )
        if not outcomes:
            clarifications.append(
                {
                    "id": "observable_outcome",
                    "category": "policy_choice",
                    "question": "完成后，哪个可观察结果能证明目标已经实现？",
                    "reason": "目标需要独立于实现方式的成功信号。",
                }
            )
        if not acceptance:
            clarifications.append(
                {
                    "id": "acceptance_criteria",
                    "category": "policy_choice",
                    "question": "请至少给出一条可验证的验收标准。",
                    "reason": "没有验收标准时，执行 Agent 同时定义问题和成功。",
                }
            )
        if not non_goals:
            clarifications.append(
                {
                    "id": "non_goals",
                    "category": "policy_choice",
                    "question": "本次明确不做什么，哪些相邻改进应留到以后？",
                    "reason": "非目标用于阻止执行过程中的隐性扩张。",
                }
            )
        if not deterministic_checks:
            clarifications.append(
                {
                    "id": "deterministic_evidence",
                    "category": "engineering_invariant",
                    "question": "哪些确定性命令或检查必须通过，才能证明本次交付完成？",
                    "reason": "没有预先声明的确定性证据时，完成结论会退化为 Agent 自证。",
                }
            )
        if (set(risk_flags) & (CRITICAL_FLAGS | HIGH_FLAGS)) and not human_decisions:
            clarifications.append(
                {
                    "id": "human_decisions",
                    "category": "policy_choice",
                    "question": "高风险事项中哪些选择和最终动作必须由人确认？",
                    "reason": "安全、数据、计费和不可逆动作不能由执行者自行授权。",
                }
            )

        for uncertainty in uncertainty_registry:
            if not uncertainty["blocking"]:
                continue
            clarifications.append(
                {
                    "id": uncertainty["id"],
                    "decision_id": uncertainty["decision_id"],
                    "category": uncertainty["category"],
                    "impact": uncertainty["impact"],
                    "acceptance_ids": uncertainty["acceptance_ids"],
                    "question": uncertainty["question"]
                    or "请确认：%s" % uncertainty["statement"],
                    "reason": uncertainty["consequence"]
                    or (
                        "这是执行者无权自行选择的政策问题。"
                        if uncertainty["category"] == "policy_choice"
                        else "这是需要权威证据或专家确认的领域事实。"
                    ),
                    "proposed_default": uncertainty["proposed_default"],
                    "decision_owner": uncertainty["decision_owner"],
                }
            )

        uncertainty_by_id = {
            item["decision_id"]: item for item in uncertainty_registry
        }
        for question in clarifications:
            source_uncertainty = uncertainty_by_id.get(str(question["id"])) or {}
            question.setdefault("decision_id", question["id"])
            question.setdefault(
                "state", source_uncertainty.get("state", "unknown")
            )
            question.setdefault(
                "impact", source_uncertainty.get("impact", "high")
            )
            question.setdefault(
                "acceptance_ids", source_uncertainty.get("acceptance_ids", [])
            )
            question.setdefault(
                "consequence",
                source_uncertainty.get("consequence", question.get("reason", "")),
            )
            question.setdefault(
                "proposed_default",
                source_uncertainty.get("proposed_default", ""),
            )
            question.setdefault(
                "decision_owner",
                source_uncertainty.get(
                    "decision_owner",
                    "domain_expert"
                    if question.get("category") == "domain_fact"
                    else (
                        "deterministic_rule"
                        if question.get("category") == "engineering_invariant"
                        else "human"
                    ),
                ),
            )
            question.setdefault(
                "reversible", bool(source_uncertainty.get("reversible", False))
            )
            question.setdefault("answer", source_uncertainty.get("answer", ""))
            question["blocking"] = True

        immutable = {
            "title": title,
            "goal": goal,
            "users": users,
            "outcomes": outcomes,
            "acceptance_criteria": acceptance,
            "non_goals": non_goals,
            "constraints": constraints,
            "forbidden_behaviors": forbidden,
            "human_decisions": human_decisions,
            "deterministic_checks": deterministic_checks,
            "change_types": change_types,
            "intent_alignment": intent_alignment,
            "explicit_risk_flags": explicit_flags,
            "risk_flags": risk_flags,
            "environment": environment,
            "uncertainty_registry": uncertainty_registry,
            "required_evidence_classes": required_evidence_classes,
            "must_kill_cases": must_kill_cases,
        }
        digest = _canonical_hash(immutable)
        return {
            "schema_version": CONTRACT_VERSION,
            "contract_id": "ctr_%s" % digest[:16],
            "contract_hash": digest,
            "status": "needs_clarification" if clarifications else "ready",
            **immutable,
            "risk": {
                "level": level,
                "flags": risk_flags,
                "explicit_flags": explicit_flags,
                "inferred_flags": inferred,
                "note": "Inferred flags are routing signals, not proof of a defect.",
            },
            "clarifications": clarifications,
            "question_gate": {
                "schema_version": CONTRACT_VERSION,
                "policy": "Block only high-impact unresolved, contested, blocked, or unsafe assumed policy/domain decisions; discover engineering invariants and researchable facts without interrupting the human.",
                "blocking_questions": clarifications,
                "non_blocking_routes": [
                    item for item in uncertainty_registry if not item["blocking"]
                ],
                "states": [
                    "known",
                    "assumed",
                    "unknown",
                    "contested",
                    "delegated",
                    "blocked",
                ],
                "resume_protocol": [
                    "emit_question_bundle",
                    "pause_before_owner_creation",
                    "record_human_or_expert_answer",
                    "compile_hash_bound_contract_delta",
                    "resume_the_same_external_task_with_the_new_contract_hash",
                ],
            },
            "change_control": {
                "rule": "Any change to immutable contract fields creates a new contract hash.",
                "decision_log_required": True,
            },
        }

    def propose_contract_resolution(
        self,
        contract: Mapping[str, Any],
        answers: Sequence[Mapping[str, Any]],
        field_updates: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Compile a hash-bound question resolution without persisting or approving it.

        The result is a proposal for a trusted human-facing controller.  It
        deliberately cannot attest that ``answered_by`` is a real person and
        therefore never resumes an external task by itself.
        """
        self._validate_contract(contract)
        if isinstance(answers, (str, bytes)) or not isinstance(answers, Iterable):
            raise ValueError("answers must be an array of objects")
        source = {
            "title": contract.get("title"),
            "goal": contract.get("goal"),
            "users": list(contract.get("users") or []),
            "outcomes": list(contract.get("outcomes") or []),
            "acceptance_criteria": list(contract.get("acceptance_criteria") or []),
            "non_goals": list(contract.get("non_goals") or []),
            "constraints": list(contract.get("constraints") or []),
            "forbidden_behaviors": list(contract.get("forbidden_behaviors") or []),
            "human_decisions": list(contract.get("human_decisions") or []),
            "deterministic_checks": list(contract.get("deterministic_checks") or []),
            "change_types": list(contract.get("change_types") or []),
            "risk_flags": list(contract.get("explicit_risk_flags") or []),
            "environment": copy.deepcopy(contract.get("environment") or {}),
            "uncertainties": copy.deepcopy(contract.get("uncertainty_registry") or []),
            "required_evidence_classes": list(
                contract.get("required_evidence_classes") or []
            ),
            "must_kill_cases": copy.deepcopy(contract.get("must_kill_cases") or []),
            "intent_alignment": copy.deepcopy(contract.get("intent_alignment") or {}),
        }
        allowed_updates = {
            "users",
            "outcomes",
            "acceptance_criteria",
            "non_goals",
            "human_decisions",
            "deterministic_checks",
        }
        updates = dict(field_updates or {})
        unknown_updates = sorted(set(updates) - allowed_updates)
        if unknown_updates:
            raise ValueError(
                "field_updates cannot change: %s" % ", ".join(unknown_updates)
            )
        field_delta = []
        for field, value in updates.items():
            normalized = _strings(value, "field_updates.%s" % field)
            if normalized != source[field]:
                field_delta.append(
                    {"field": field, "before": source[field], "after": normalized}
                )
                source[field] = normalized

        by_id = {
            str(item.get("decision_id") or item.get("id")): item
            for item in source["uncertainties"]
        }
        decision_delta = []
        seen_answers = set()
        for raw in list(answers)[:100]:
            if not isinstance(raw, Mapping):
                raise ValueError("answers entries must be objects")
            decision_id = _text(
                raw.get("decision_id"), "answer decision_id", required=True, limit=120
            )
            if decision_id in seen_answers:
                raise ValueError("duplicate answer decision_id: %s" % decision_id)
            seen_answers.add(decision_id)
            uncertainty = by_id.get(decision_id)
            if uncertainty is None:
                raise ValueError("answer refers to unknown decision_id: %s" % decision_id)
            answer = _text(raw.get("answer"), "answer", required=True, limit=4000)
            answered_by = _text(
                raw.get("answered_by"), "answered_by", required=True, limit=200
            )
            authority = _text(raw.get("authority"), "answer authority", limit=40) or (
                "domain_expert"
                if uncertainty.get("category") == "domain_fact"
                else "human"
            )
            if authority not in {"human", "domain_expert"}:
                raise ValueError("answers require human or domain_expert authority")
            if uncertainty.get("category") == "policy_choice" and authority != "human":
                raise ValueError("policy choices require human authority")
            delegated = bool(raw.get("delegated", False))
            new_state = (
                "delegated"
                if delegated
                else (
                    "expert_confirmed"
                    if uncertainty.get("category") == "domain_fact"
                    else "resolved"
                )
            )
            before = {
                "state": uncertainty.get("state", uncertainty.get("status")),
                "answer": uncertainty.get("answer", ""),
                "evidence": uncertainty.get("evidence", ""),
            }
            uncertainty["state"] = new_state
            uncertainty["status"] = new_state
            uncertainty["answer"] = answer
            uncertainty["decision_owner"] = "human" if delegated else authority
            uncertainty["evidence"] = _text(
                raw.get("evidence"), "answer evidence", limit=4000
            ) or "Answer recorded from %s" % answered_by
            decision_delta.append(
                {
                    "decision_id": decision_id,
                    "answered_by": answered_by,
                    "authority": authority,
                    "before": before,
                    "after": {
                        "state": new_state,
                        "answer": answer,
                        "evidence": uncertainty["evidence"],
                    },
                }
            )

        proposed = self.compile_contract(source)
        if not field_delta and not decision_delta:
            raise ValueError("contract resolution must change a field or answer a decision")
        delta = {
            "parent_contract_hash": contract["contract_hash"],
            "proposed_contract_hash": proposed["contract_hash"],
            "field_updates": field_delta,
            "decision_updates": decision_delta,
        }
        return {
            "schema_version": CONTRACT_VERSION,
            "status": "awaiting_human_attestation",
            "delta": delta,
            "delta_hash": _canonical_hash(delta),
            "proposed_contract": proposed,
            "resume": {
                "allowed_now": False,
                "condition": "trusted controller attests this delta and the proposed contract is ready",
                "reuse_external_task": True,
                "owner_must_not_exist_before_ready": True,
            },
        }

    def route(
        self,
        contract: Mapping[str, Any],
        bad_case_registry: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._validate_contract(contract)
        compiled_registry = (
            validate_bad_case_registry(bad_case_registry)
            if bad_case_registry is not None
            else None
        )
        public_cases = list(contract.get("must_kill_cases") or [])
        hidden_cases = list((compiled_registry or {}).get("must_kill_cases") or [])
        public_ids = {str(item.get("id")) for item in public_cases}
        collisions = sorted(
            str(item.get("id")) for item in hidden_cases if str(item.get("id")) in public_ids
        )
        if collisions:
            raise ValueError(
                "bad case ids collide with public must-kill cases: %s"
                % ", ".join(collisions)
            )
        all_must_kill_cases = public_cases + hidden_cases
        risk = contract.get("risk") or {}
        level = str(risk.get("level", "medium"))
        flags = set(risk.get("flags") or [])
        lanes: List[LaneDefinition] = [
            LaneDefinition(
                "deterministic-ci",
                "deterministic",
                "Run build, formatting, lint, type, unit, integration, and configured scanners.",
                ("repository_snapshot", "contract", "configured_commands"),
                "Any required deterministic check that fails or is silently skipped blocks.",
                "Deterministic evidence always runs before model judgment.",
                fresh_context=False,
            )
        ]

        if RISK_ORDER[level] >= RISK_ORDER["medium"]:
            lanes.extend(
                [
                    LaneDefinition(
                        "contract-domain-semantics",
                        "semantic",
                        "Falsify contract conformance and domain semantics, including unresolved facts and prohibited conclusions.",
                        ("contract", "domain_evidence", "diff", "deterministic_results"),
                        "A reproducible contract or domain-semantic violation with authoritative evidence blocks.",
                        "This is the first default orthogonal failure mode: intended and domain-correct behavior.",
                    ),
                    LaneDefinition(
                        "state-trust-boundaries",
                        "semantic",
                        "Trace state transitions, typed data, authority, replay, permissions, and repository invariants.",
                        ("contract", "diff", "project_rules", "state_model", "trust_model"),
                        "A reproducible state-integrity or trust-boundary violation blocks.",
                        "This is the second default orthogonal failure mode: state and authority integrity.",
                    ),
                    LaneDefinition(
                        "test-oracle-falsification",
                        "semantic",
                        "Falsify the test oracle with mutation, boundary, property, false-mock, and missing-failure probes.",
                        ("contract", "diff", "tests", "deterministic_results", "must_kill_cases"),
                        "An oracle that survives a material mutation or misses a must-kill counterexample blocks.",
                        "This is the third default orthogonal failure mode: whether evidence can detect wrong code.",
                    ),
                ]
            )

        if flags & {
            "authentication",
            "authorization",
            "billing",
            "external_write",
            "multi_tenant",
            "privacy",
            "secrets",
            "supply_chain",
        }:
            lanes.append(
                LaneDefinition(
                    "security",
                    "semantic",
                    "Trace attacker-controlled input, permissions, secrets, dependencies, and exploitability.",
                    ("contract", "diff", "threat_model", "scanner_artifacts", "relevant_code"),
                    "Only high-confidence exploitable findings or deterministic scanner failures block.",
                    "The change touches a security or trust boundary.",
                )
            )

        if flags & {"data_migration", "data_deletion", "multi_tenant", "schema_change", "public_api"}:
            lanes.append(
                LaneDefinition(
                    "data-compatibility",
                    "semantic",
                    "Check data ownership, migration reversibility, API/schema compatibility, and rollback.",
                    ("contract", "diff", "schema", "migration_plan", "compatibility_tests"),
                    "Irreversible loss, cross-tenant exposure, or unhandled compatibility break blocks.",
                    "The change can outlive or cross a single code deployment.",
                )
            )

        if "user_experience" in flags:
            lanes.append(
                LaneDefinition(
                    "e2e-ux",
                    "runtime",
                    "Exercise critical user flows in a real browser, including accessibility and visual evidence.",
                    ("contract", "test_environment", "seed_state", "browser_artifacts"),
                    "A failed critical flow or inaccessible required interaction blocks.",
                    "The change has observable user-interface behavior.",
                )
            )

        if "performance" in flags or RISK_ORDER[level] >= RISK_ORDER["high"]:
            lanes.append(
                LaneDefinition(
                    "reliability-cost",
                    "runtime",
                    "Check latency, resource use, failure recovery, concurrency, and execution budgets.",
                    ("contract", "benchmarks", "runtime_logs", "budget"),
                    "A stated SLO or bounded-execution guarantee that regresses blocks.",
                    "High-risk delivery needs operational evidence, not only code inspection.",
                )
            )

        if RISK_ORDER[level] >= RISK_ORDER["high"]:
            lanes.append(
                LaneDefinition(
                    "adversarial-falsification",
                    "semantic",
                    "Try to disprove completion using boundary, abuse, recovery, and conflicting-state scenarios.",
                    ("contract", "diff", "accepted_evidence"),
                    "A reproducible critical or high-severity counterexample blocks.",
                    "High-risk work warrants an intentionally opposing context.",
                )
            )

        alignment = contract.get("intent_alignment") or {}
        technology_strategy = alignment.get("technology_strategy") or {}
        race_enabled = technology_strategy.get("mode") == "bounded_race"
        selected_race_paths = list(technology_strategy.get("selected_path_ids") or [])
        human_gates = ["final_merge_or_release"]
        if alignment.get("required"):
            human_gates.insert(0, "intent_alignment_attestation")
        if race_enabled:
            human_gates.insert(-1, "race_human_selection")
        if contract.get("status") != "ready":
            human_gates.insert(0, "contract_clarification")
        if RISK_ORDER[level] >= RISK_ORDER["high"]:
            human_gates.insert(-1, "high_risk_policy_and_release")
        if "user_experience" in flags:
            human_gates.insert(-1, "experience_and_taste")

        sequence = ["environment_preflight"]
        if race_enabled:
            sequence.extend(
                [
                    "parallel_technology_race",
                    "race_evaluation",
                    "race_human_selection",
                ]
            )
        sequence.extend(
            [
                "owner_implementation",
                "deterministic_ci",
                "independent_inspection",
                "adjudication",
                "single_consolidated_repair",
                "full_reverification",
                "blind_final_verification",
                "human_handoff",
            ]
        )
        artifact_invariants = {
            "environment_preflight": [
                "environment capsule is ready and bound to the contract",
                "assigned repository root equals the real Git diff root",
                "required commands, ports, permissions, and locks are healthy",
            ],
            "owner_implementation": [
                "owner context id and real diff digest exist",
                "a retained race winner continues its original context; fusion uses the explicitly assigned integration owner",
                "no external action was performed",
            ],
            "deterministic_ci": [
                "every declared check and required evidence class has reproducible passing evidence"
            ],
            "independent_inspection": [
                "every enabled lane submitted a fresh read-only evidence packet without transcript or peer leakage"
            ],
            "adjudication": [
                "verdict is hash-bound and cross-lane findings are merged by root cause"
            ],
            "single_consolidated_repair": [
                "only the original owner performs at most one consolidated repair"
            ],
            "full_reverification": [
                "the complete deterministic and semantic plan reruns after repair"
            ],
            "blind_final_verification": [
                "a fresh read-only verifier blind to transcripts and prior findings passes every must-kill case"
            ],
            "human_handoff": [
                "automation stops without approving merge or release"
            ],
        }
        if race_enabled:
            artifact_invariants.update(
                {
                    "parallel_technology_race": [
                        "exactly two or three selected paths run in unique contexts and isolated worktrees",
                        "every path is bound to the same contract, research, frozen data, test commands, dimensions, and budget",
                        "candidate implementations and transcripts remain mutually invisible before submission",
                    ],
                    "race_evaluation": [
                        "one fresh read-only evaluator applies the same frozen tests and dimensions to every path",
                        "the evaluator sees candidate artifacts and diffs but no lane transcript or peer conclusion",
                        "the only recommendation is keep one path, fuse explicit benefits, or reject all",
                    ],
                    "race_human_selection": [
                        "a trusted controller HMAC binds the human decision to the evaluation hash",
                        "fusion occurs only when pre-authorized and names an isolated integration owner",
                        "reject-all stops before owner implementation",
                    ],
                }
            )

        plan = {
            "schema_version": PLAN_VERSION,
            "contract_id": contract["contract_id"],
            "contract_hash": contract["contract_hash"],
            "risk_level": level,
            "intent_alignment": {
                "required": bool(
                    (contract.get("intent_alignment") or {}).get("required")
                ),
                "intent_hash": str(
                    (contract.get("intent_alignment") or {}).get("intent_hash") or ""
                ),
                "inspection_hash": str(
                    alignment.get("inspection_hash") or ""
                ),
                "research_hash": str(alignment.get("research_hash") or ""),
                "technology_strategy_hash": str(
                    alignment.get("technology_strategy_hash") or ""
                ),
                "human_attestation_required": bool(
                    (contract.get("intent_alignment") or {}).get(
                        "human_attestation_required"
                    )
                ),
                "exemption_reason": str(
                    (contract.get("intent_alignment") or {}).get("exemption_reason") or ""
                ),
            },
            "execution": {
                "owner_context": "continuous",
                "owner_write_access": True,
                "owner_creation_requires": (
                    "valid_intent_alignment_attestation"
                    if (contract.get("intent_alignment") or {}).get("required")
                    else "documented_policy_exemption"
                ),
                "selection": "single owner by default; fan out only the enabled independent lanes",
                "max_parallel_inspectors": min(4, max(1, len(lanes) - 1)),
            },
            "technology_strategy": copy.deepcopy(technology_strategy),
            "technology_race": {
                "enabled": race_enabled,
                "selected_path_ids": selected_race_paths,
                "research_hash": str(alignment.get("research_hash") or ""),
                "strategy_hash": str(
                    alignment.get("technology_strategy_hash") or ""
                ),
                "common_test_suite_hash": (
                    _canonical_hash(
                        {"commands": technology_strategy.get("common_test_commands") or []}
                    )
                    if race_enabled
                    else ""
                ),
                "evaluation_dimensions_hash": (
                    _canonical_hash(
                        {
                            "dimensions": technology_strategy.get(
                                "evaluation_dimensions"
                            )
                            or []
                        }
                    )
                    if race_enabled
                    else ""
                ),
                "isolation": "one unique context and Git worktree per path",
                "cross_lane_visibility_before_submission": False,
                "evaluator": "fresh_read_only_blind_to_lane_transcripts",
                "human_selection_required": race_enabled,
                "fusion_allowed": bool(technology_strategy.get("fusion_allowed", False)),
            },
            "sequence": sequence,
            "required_checks": list(contract.get("deterministic_checks") or []),
            "required_evidence_classes": list(
                contract.get("required_evidence_classes") or []
            ),
            "must_kill_cases": all_must_kill_cases,
            "bad_case_registry": compiled_registry,
            "hidden_case_ids": [item["id"] for item in hidden_cases],
            "artifact_invariants": artifact_invariants,
            "lanes": [lane.to_dict() for lane in lanes],
            "repair_policy": {
                "max_automatic_rounds": 1,
                "repair_owner": "original_owner_context",
                "inspectors_may_edit": False,
                "after_limit": "human_decision",
            },
            "healer_policy": {
                "allowed": [
                    "locator updates proven equivalent",
                    "bounded waits tied to observable readiness",
                    "test fixtures and test infrastructure",
                ],
                "forbidden": [
                    "skip or delete a failing test",
                    "weaken an assertion or expected behavior",
                    "hide a product defect as test flakiness",
                    "edit product code from an inspector context",
                ],
            },
            "final_verifier": {
                "context": "new_blind_context",
                "repository": "read_only",
                "owner_transcript_visible": False,
                "prior_findings_visible": False,
                "runs_after": "full_reverification",
                "required_cases": [item["id"] for item in all_must_kill_cases],
                "failure": "human_decision",
            },
            "checkpoint_policy": {
                "atomic": True,
                "single_writer": True,
                "event_chain": "controller HMAC; a content hash alone is not authenticity",
                "resume": "replay signed events and artifact invariants, then continue at the first pending stage",
                "agent_access_to_control_token": False,
            },
            "inspector_calibration_policy": {
                "baseline_plan_lanes": "blocking",
                "new_or_changed_inspector": "shadow_until_a_hash_bound_profile_passes_all_thresholds",
                "profile_source": "human-labelled Good/Bad Cases",
                "automatic_promotion": False,
                "blocking_never_means_merge_or_release_authority": True,
            },
            "telemetry_schema": {
                "per_stage": [
                    "duration_ms",
                    "input_tokens",
                    "output_tokens",
                    "human_actions",
                    "control_plane_recoveries",
                ],
                "per_inspector": [
                    "submitted_findings",
                    "independent_defect_contributions",
                    "unique_defect_contributions",
                    "filtered_findings",
                    "false_positives",
                    "duration_ms",
                    "input_tokens",
                    "output_tokens",
                ],
            },
            "human_gates": human_gates,
            "stop_conditions": [
                "contract is not ready",
                "required intent alignment lacks a valid human attestation",
                "race evaluation is not bound to identical tests and dimensions",
                "human rejects all race candidates or does not attest a winner/fusion",
                "required deterministic evidence is unavailable",
                "one consolidated repair did not converge",
                "inspectors disagree on a high-risk fact",
                "an irreversible or external action is next",
            ],
        }
        return {**plan, "plan_hash": _canonical_hash(plan)}

    def context_packets(
        self, contract: Mapping[str, Any], plan: Mapping[str, Any]
    ) -> List[Dict[str, Any]]:
        self._validate_plan(contract, plan)
        packets = []
        for lane in plan.get("lanes") or []:
            if lane.get("kind") == "deterministic":
                continue
            packets.append(
                {
                    "lane_id": lane["id"],
                    "display_name": lane.get("display_name") or lane["id"],
                    "mission": lane.get("mission") or lane["purpose"],
                    "contract_id": contract["contract_id"],
                    "contract_hash": contract["contract_hash"],
                    "goal": contract["goal"],
                    "intent_alignment": copy.deepcopy(
                        contract.get("intent_alignment") or {}
                    ),
                    "acceptance_criteria": contract["acceptance_criteria"],
                    "non_goals": contract["non_goals"],
                    "constraints": contract["constraints"],
                    "forbidden_behaviors": contract["forbidden_behaviors"],
                    "uncertainty_registry": contract.get("uncertainty_registry") or [],
                    "must_kill_cases": plan.get("must_kill_cases") or [],
                    "hidden_case_ids": plan.get("hidden_case_ids") or [],
                    "purpose": lane["purpose"],
                    "required_inputs": lane["required_inputs"],
                    "blocking_rule": lane["blocking_rule"],
                    "permissions": {"repository": "read", "external_writes": False},
                    "isolation": {
                        "fresh_context": True,
                        "developer_transcript_visible": False,
                        "peer_findings_visible_before_submission": False,
                    },
                    "output_contract": {
                        "required": [
                            "id",
                            "title",
                            "severity",
                            "confidence",
                            "root_cause_key",
                            "violated_invariant",
                            "counterexample",
                            "reproduction",
                            "artifact_refs",
                            "introduced_by_change",
                        ],
                        "reproduction_shape": {
                            "preconditions": "array",
                            "steps": "array",
                            "expected": "string",
                            "actual": "string",
                        },
                        "high_signal_only": True,
                    },
                }
            )
        return packets

    def delivery_handoff(
        self, contract: Mapping[str, Any], plan: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Build a pure execution manifest for a mature control plane.

        The manifest intentionally does not call Kandev or start Codex. It is
        the deterministic boundary that lets a human surface or external MCP
        client create the right sessions without reinterpreting the policy.
        """
        self._validate_plan(contract, plan)
        if contract.get("status") != "ready":
            return {
                "schema_version": CONTRACT_VERSION,
                "contract_id": contract["contract_id"],
                "contract_hash": contract["contract_hash"],
                "plan_hash": plan["plan_hash"],
                "status": "blocked_on_clarification",
                "next_action": "return_to_human_intake",
                "clarifications": contract.get("clarifications") or [],
                "question_gate": contract.get("question_gate") or {},
                "resume_requires": "trusted_controller_attestation_bound_to_the_same_external_task",
                "external_actions_allowed": False,
            }

        alignment = contract.get("intent_alignment") or {}
        intent_attestation_required = bool(
            alignment.get("required") and alignment.get("human_attestation_required")
        )
        brief = alignment.get("brief") or {}
        inspection = alignment.get("inspection") or {}
        technology_research = brief.get("technology_research") or {}
        technology_strategy = brief.get("technology_strategy") or {}
        race_enabled = technology_strategy.get("mode") == "bounded_race"
        research_paths = {
            str(item.get("id")): item
            for item in technology_research.get("technology_paths") or []
            if isinstance(item, Mapping)
        }
        contexts = self.context_packets(contract, plan)
        owner_instructions = [
            "Treat the compiled contract and its hash as immutable for this attempt.",
            "Treat the intent brief and independent intent inspection as binding inputs; stop if implementation would contradict either artifact.",
            "Treat the frozen technology research, human-selected strategy, and any signed race decision as binding; do not silently substitute another framework or path.",
            "Do not begin until the environment capsule is ready and proves the assigned repository is the real diff root.",
            "Use one continuous Codex session for investigation, implementation, and at most one consolidated repair.",
            "Work only in the assigned isolated worktree and keep changes inside the stated goal and non-goals.",
            "Run every declared deterministic check and preserve its command, status, and evidence.",
            "Do not push, merge, deploy, publish, contact external people, purchase usage, or consume reset credits.",
            "Never put an API key in governance input, prompts, logs, or source; an explicitly approved product runtime may read its own key from an environment variable.",
            "Stop for a human on ambiguity, policy, taste, permission expansion, or an irreversible/external action.",
            "A stage completes only when its artifact invariants pass; prose completion and process exit are not evidence.",
        ]
        inspectors = []
        for context in contexts:
            inspectors.append(
                {
                    "title": str(context["display_name"])[:60],
                    "display_name": context["display_name"],
                    "mission": context["mission"],
                    "lane_id": context["lane_id"],
                    "activation": "after_all_required_deterministic_checks_pass",
                    "session": "new_task_and_fresh_session",
                    "profile_requirement": "Codex inspector profile with repository read-only and no external writes",
                    "context": context,
                    "monitoring_contract": {
                        "enforcement_mode": "blocking",
                        "execution_state": "queued",
                        "progress_summary": "等待确定性门禁通过",
                        "current_difficulty": "无；尚未启动",
                        "last_heartbeat_at": "",
                        "needs_human": False,
                        "dependency": "deterministic_ci",
                    },
                }
            )

        race_tasks = []
        chinese_numbers = ("一", "二", "三")
        for index, path_id in enumerate(technology_strategy.get("selected_path_ids") or []):
            path = research_paths.get(str(path_id)) or {}
            race_tasks.append(
                {
                    "id": "technology-race-%s" % path_id,
                    "path_id": path_id,
                    "display_name": "技术赛道%s" % chinese_numbers[index],
                    "mission": str(path.get("name") or path.get("approach") or path_id),
                    "session": "unique_context_and_isolated_worktree",
                    "activation": "after_intent_attestation_and_environment_preflight",
                    "frozen_inputs": {
                        "contract_hash": contract["contract_hash"],
                        "research_hash": alignment.get("research_hash") or "",
                        "strategy_hash": alignment.get("technology_strategy_hash") or "",
                        "common_test_commands": list(
                            technology_strategy.get("common_test_commands") or []
                        ),
                        "evaluation_dimensions": list(
                            technology_strategy.get("evaluation_dimensions") or []
                        ),
                        "time_budget_minutes": technology_strategy.get(
                            "time_budget_minutes", 0
                        ),
                        "cost_budget": technology_strategy.get("cost_budget") or "",
                    },
                    "cross_lane_visibility_before_submission": False,
                    "external_actions_allowed": False,
                    "monitoring_contract": {
                        "enforcement_mode": "not_applicable",
                        "execution_state": (
                            "waiting_on_human"
                            if intent_attestation_required
                            else "waiting_on_dependency"
                        ),
                        "progress_summary": (
                            "等待人类意图签署"
                            if intent_attestation_required
                            else "等待环境预检"
                        ),
                        "current_difficulty": (
                            "不得在路线、预算和统一测试未签署时启动"
                            if intent_attestation_required
                            else "无；尚未启动"
                        ),
                        "last_heartbeat_at": "",
                        "needs_human": intent_attestation_required,
                        "dependency": (
                            "intent_attestation"
                            if intent_attestation_required
                            else "environment_preflight"
                        ),
                    },
                }
            )

        race_evaluator_task = {
            "id": "technology-race-evaluator",
            "display_name": "统一赛马评测员",
            "mission": "在全新只读上下文用同一冻结测试和维度比较所有赛道，只建议保留、明确融合或全部淘汰",
            "activation": "after_all_race_paths_submit",
            "session": "new_task_fresh_read_only",
            "candidate_transcripts_visible": False,
            "peer_findings_visible": False,
            "monitoring_contract": {
                "enforcement_mode": "blocking",
                "execution_state": "waiting_on_dependency" if race_enabled else "finished",
                "progress_summary": "等待全部赛道提交" if race_enabled else "未启用赛马",
                "current_difficulty": "无；尚未启动" if race_enabled else "无",
                "last_heartbeat_at": "",
                "needs_human": False,
                "dependency": "parallel_technology_race" if race_enabled else "",
            },
        }
        race_human_task = {
            "id": "technology-race-human-selection",
            "display_name": "技术路线裁决",
            "mission": "由人根据统一评测决定保留某一路、融合明确优点或全部不合格",
            "activation": "after_hash_bound_race_evaluation",
            "monitoring_contract": {
                "enforcement_mode": "not_applicable",
                "execution_state": "waiting_on_dependency" if race_enabled else "finished",
                "progress_summary": "等待统一评测" if race_enabled else "未启用赛马",
                "current_difficulty": "最终路线不能由 Agent 代选" if race_enabled else "无",
                "last_heartbeat_at": "",
                "needs_human": race_enabled,
                "dependency": "race_evaluation" if race_enabled else "",
            },
        }

        return {
            "schema_version": CONTRACT_VERSION,
            "contract_id": contract["contract_id"],
            "contract_hash": contract["contract_hash"],
            "plan_hash": plan["plan_hash"],
            "status": (
                "awaiting_intent_attestation"
                if intent_attestation_required
                else "ready_for_control_plane"
            ),
            "next_action": (
                "trusted_controller_attests_displayed_intent_before_owner_creation"
                if intent_attestation_required
                else "create_owner_after_environment_preflight"
            ),
            "target": "Kandev",
            "executor": {
                "agent": "Codex",
                "authentication": "locally authenticated ChatGPT subscription under the Kandev service user",
                "api_key_allowed": False,
            },
            "product_runtime": copy.deepcopy(
                brief.get("product_runtime")
                or {
                    "provider": "not_applicable",
                    "model": "",
                    "authentication": "",
                    "purpose": "No separate product runtime was declared.",
                }
            ),
            "intent_gate": {
                "required": intent_attestation_required,
                "owner_creation_allowed": not intent_attestation_required and not race_enabled,
                "activation_condition": (
                    "valid controller HMAC attestation binds research_hash, technology_strategy_hash, intent_hash, inspection_hash, contract_hash, plan_hash, and external_task_ref"
                    if intent_attestation_required
                    else "policy exemption recorded on the contract"
                ),
                "intent_hash": alignment.get("intent_hash") or "",
                "inspection_hash": alignment.get("inspection_hash") or "",
                "research_hash": alignment.get("research_hash") or "",
                "technology_strategy_hash": alignment.get(
                    "technology_strategy_hash"
                )
                or "",
                "exemption_reason": alignment.get("exemption_reason") or "",
                "tasks": [
                    {
                        "id": "intent-confirmer",
                        "display_name": "意图确认员",
                        "mission": "向人确认最终结果、技术调研、路线/赛马预算、开发执行器、产品运行时、验收样例和风险边界",
                        "execution_state": (
                            "waiting_on_human" if intent_attestation_required else "finished"
                        ),
                        "progress_summary": (
                            "意图简报和独立检查已完成，等待人类签署"
                            if intent_attestation_required
                            else "本任务按低风险政策豁免"
                        ),
                        "current_difficulty": (
                            "必须由人确认，Agent 不能代签"
                            if intent_attestation_required
                            else "无"
                        ),
                        "needs_human": intent_attestation_required,
                    },
                    {
                        "id": "intent-inspector",
                        "display_name": "意图检查员",
                        "mission": "全新只读上下文独立查找目标偷换、遗漏、供应商混淆、未确认默认和不可证明验收",
                        "execution_state": "finished" if inspection else "waiting_on_dependency",
                        "progress_summary": (
                            "独立意图检查通过"
                            if inspection.get("status") == "pass"
                            else "等待意图检查完成"
                        ),
                        "current_difficulty": "无" if inspection.get("status") == "pass" else "缺少通过的检查证据",
                        "needs_human": False,
                        "fresh_context": True,
                        "read_only": True,
                    },
                ],
            },
            "technology_research_gate": {
                "required": bool(alignment.get("required")),
                "status": technology_research.get("status") or "policy_exempt",
                "research_hash": alignment.get("research_hash") or "",
                "tasks": [
                    {
                        "id": "technology-researcher",
                        "display_name": "技术调研员",
                        "mission": "检索社区实践、近期高质量学术证据、开源框架和官方一手资料，形成多条路径",
                        "execution_state": "finished" if technology_research else "waiting_on_dependency",
                        "progress_summary": "四路调研证据已冻结" if technology_research else "等待调研",
                        "current_difficulty": "无" if technology_research else "缺少调研证据",
                        "needs_human": False,
                    },
                    {
                        "id": "research-quality-inspector",
                        "display_name": "调研质检员",
                        "mission": "在全新只读上下文检查来源质量、时效、适配性、选择偏差和赛马必要性",
                        "execution_state": "finished" if technology_research.get("status") == "pass" else "waiting_on_dependency",
                        "progress_summary": "独立调研质检通过" if technology_research.get("status") == "pass" else "等待质检通过",
                        "current_difficulty": "无" if technology_research.get("status") == "pass" else "调研仍有阻塞项",
                        "needs_human": False,
                        "fresh_context": True,
                        "read_only": True,
                    },
                ],
            },
            "technology_race": {
                "enabled": race_enabled,
                "strategy": copy.deepcopy(technology_strategy),
                "race_tasks": race_tasks,
                "evaluator_task": race_evaluator_task,
                "human_selection_task": race_human_task,
                "owner_creation_allowed": False if race_enabled else not intent_attestation_required,
            },
            "profile_requirements": {
                "owner": {
                    "repository": "write_in_isolated_worktree",
                    "external_writes": False,
                    "auto_approve_all_permissions": False,
                },
                "inspector": {
                    "repository": "read_only",
                    "external_writes": False,
                    "auto_approve_all_permissions": False,
                },
            },
            "preflight": {
                "required": True,
                "artifact": "environment_capsule",
                "checks": [
                    "cwd and real Git diff root",
                    "read/write permissions",
                    "required PATH commands",
                    "required loopback ports",
                    "Git write locks",
                ],
                "on_failure": "honest_stop",
            },
            "checkpoint_ledger": {
                "owner": "trusted_control_plane_not_an_agent",
                "protocol": plan["checkpoint_policy"],
                "control_token_visibility": "controller_only",
                "artifact_invariants": plan["artifact_invariants"],
            },
            "owner_task": {
                "title": ("主实现：%s" % contract["title"])[:60],
                "display_name": "主实现者",
                "mission": "调查、实现、自测，并在原上下文完成一次统一修复",
                "creation_allowed": not intent_attestation_required and not race_enabled,
                "activation": (
                    "after_valid_human_race_selection"
                    if race_enabled
                    else (
                        "after_valid_intent_attestation_and_environment_preflight"
                        if intent_attestation_required
                        else "after_environment_preflight"
                    )
                ),
                "session": "continuous_until_handoff_or_single_repair",
                "contract": contract,
                "instructions": owner_instructions,
                "required_output": {
                    "diff": True,
                    "deterministic_results": contract["deterministic_checks"],
                    "decision_log": True,
                    "external_actions_performed": [],
                },
                "monitoring_contract": {
                    "enforcement_mode": "not_applicable",
                    "execution_state": (
                        "waiting_on_human"
                        if intent_attestation_required
                        else ("waiting_on_dependency" if race_enabled else "queued")
                    ),
                    "progress_summary": (
                        "等待人类意图签署"
                        if intent_attestation_required
                        else ("等待技术赛马裁决" if race_enabled else "等待环境预检")
                    ),
                    "current_difficulty": (
                        "意图尚未由可信控制器记录人类确认"
                        if intent_attestation_required
                        else ("统一赛马和人工选路尚未完成" if race_enabled else "无；尚未启动")
                    ),
                    "last_heartbeat_at": "",
                    "needs_human": intent_attestation_required,
                    "dependency": (
                        "intent_attestation"
                        if intent_attestation_required
                        else ("race_human_selection" if race_enabled else "environment_preflight")
                    ),
                },
            },
            "inspector_tasks": inspectors,
            "final_verifier_task": {
                "title": ("最终盲验：%s" % contract["title"])[:60],
                "display_name": "最终盲验裁决",
                "mission": "在不知道旧结论的前提下独立击杀隐藏反例并给出裁决",
                "activation": "after_full_reverification_passes",
                "session": "new_task_and_fresh_session",
                "profile_requirement": "Codex inspector profile with repository read-only and no external writes",
                "blind_to_owner_transcript": True,
                "blind_to_prior_findings": True,
                "must_kill_cases": plan.get("must_kill_cases") or [],
                "hidden_case_ids": plan.get("hidden_case_ids") or [],
                "failure": "human_decision_without_another_automatic_repair",
                "monitoring_contract": {
                    "enforcement_mode": "blocking",
                    "execution_state": "queued",
                    "progress_summary": "等待全量复验通过",
                    "current_difficulty": "无；尚未启动",
                    "last_heartbeat_at": "",
                    "needs_human": False,
                    "dependency": "full_reverification",
                },
            },
            "sequence": plan["sequence"],
            "adjudication": {
                "tool": "adjudicate_delivery",
                "repair_owner": "original_owner_context",
                "max_automatic_rounds": 1,
                "successful_handoff": "ready_for_final_verification",
            },
            "kandev": {
                "external_mcp_endpoint": "http://127.0.0.1:<kandev-port>/mcp",
                "profile_mcp_server": "ai-delivery-governance (stdio, per session)",
                "task_creation": "never create implementation before required research/strategy/intent attestation; for a bounded race create only the selected isolated race tasks, then create the retained or fusion owner only after HMAC-bound human selection; create inspectors after deterministic CI passes",
                "workflow_warning": (
                    "Do not use Kandev's stock Feature Dev workflow unchanged: its review may edit code "
                    "and its PR/CI stages can push. Import or configure a workflow that preserves this manifest's gates."
                ),
            },
            "human_gates": plan["human_gates"],
            "operator_view": {
                "name_policy": "Chinese short name plus one-sentence mission; never use the full prompt as the visible title",
                "status_dimensions": {
                    "execution": [
                        "queued",
                        "starting",
                        "working",
                        "waiting_on_dependency",
                        "waiting_on_human",
                        "finished",
                        "failed",
                        "stopped",
                    ],
                    "verdict": ["unreviewed", "pass", "warning", "blocked"],
                },
                "live_fields": [
                    "progress_summary",
                    "current_difficulty",
                    "last_heartbeat_at",
                    "needs_human",
                ],
            },
            "external_actions_allowed": False,
        }

    def adjudicate(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("adjudication input must be an object")
        contract = payload.get("contract")
        plan = payload.get("plan")
        if not isinstance(contract, Mapping) or not isinstance(plan, Mapping):
            raise ValueError("contract and plan are required objects")
        self._validate_plan(contract, plan)
        repair_round = int(payload.get("repair_round", 0))
        if repair_round < 0:
            raise ValueError("repair_round must be zero or greater")
        deterministic_results = payload.get("deterministic_results") or []
        findings = payload.get("findings") or []
        inspector_telemetry = payload.get("inspector_telemetry") or []
        inspector_calibrations = payload.get("inspector_calibrations") or []
        if (
            not isinstance(deterministic_results, list)
            or not isinstance(findings, list)
            or not isinstance(inspector_telemetry, list)
            or not isinstance(inspector_calibrations, list)
        ):
            raise ValueError(
                "deterministic_results, findings, inspector_telemetry, and inspector_calibrations must be arrays"
            )
        calibration_by_lane = calibration_modes(inspector_calibrations)

        deterministic_blockers: List[Dict[str, Any]] = []
        deterministic_summary: List[Dict[str, Any]] = []
        observed_checks = set()
        observed_evidence_classes = set()
        for raw in deterministic_results[:200]:
            if not isinstance(raw, Mapping):
                raise ValueError("deterministic result entries must be objects")
            name = _text(raw.get("name"), "deterministic result name", required=True, limit=200)
            status = _text(raw.get("status"), "deterministic result status", required=True, limit=30).lower()
            required = bool(raw.get("required", True))
            item = {
                "name": name,
                "status": status,
                "required": required,
                "command": _text(raw.get("command"), "command", limit=2000),
                "evidence": _text(raw.get("evidence"), "evidence", limit=4000),
                "class": _text(raw.get("class"), "deterministic evidence class", limit=80),
            }
            deterministic_summary.append(item)
            observed_checks.update(
                value.casefold()
                for value in (item["name"], item["command"])
                if value
            )
            if item["class"]:
                observed_evidence_classes.add(item["class"].casefold())
            if required and status not in {"passed", "pass"}:
                deterministic_blockers.append(item)

        for expected in contract.get("deterministic_checks") or []:
            if expected.casefold() in observed_checks:
                continue
            missing = {
                "name": expected,
                "status": "missing",
                "required": True,
                "command": expected,
                "evidence": "No deterministic result was submitted for this required check.",
            }
            deterministic_summary.append(missing)
            deterministic_blockers.append(missing)

        for expected_class in contract.get("required_evidence_classes") or []:
            if expected_class.casefold() in observed_evidence_classes:
                continue
            missing = {
                "name": expected_class,
                "status": "missing",
                "required": True,
                "command": "",
                "evidence": "No result was submitted for this required evidence class.",
                "class": expected_class,
            }
            deterministic_summary.append(missing)
            deterministic_blockers.append(missing)

        accepted_by_key: Dict[str, Dict[str, Any]] = {}
        rejected: List[Dict[str, Any]] = []
        disputed = False
        enabled_lanes = {
            str(item.get("id"))
            for item in plan.get("lanes") or []
            if item.get("kind") != "deterministic"
        }
        unknown_calibrations = sorted(set(calibration_by_lane) - enabled_lanes)
        if unknown_calibrations:
            raise ValueError(
                "calibration refers to a lane not enabled by the plan: %s"
                % ", ".join(unknown_calibrations)
            )
        lane_metrics: Dict[str, Dict[str, Any]] = {
            lane: {
                "submitted_findings": 0,
                "accepted_findings": 0,
                "independent_defect_contributions": 0,
                "unique_defect_contributions": 0,
                "filtered_findings": 0,
                "false_positives": 0,
                "duration_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "human_actions": 0,
                "control_plane_recoveries": 0,
                "enforcement_mode": calibration_by_lane.get(lane, {}).get(
                    "mode", "blocking"
                ),
                "calibration_profile_hash": calibration_by_lane.get(lane, {}).get(
                    "profile_hash", ""
                ),
            }
            for lane in enabled_lanes
        }
        for raw in inspector_telemetry[:100]:
            if not isinstance(raw, Mapping):
                raise ValueError("inspector telemetry entries must be objects")
            lane = _text(raw.get("lane"), "inspector telemetry lane", required=True, limit=100)
            if lane not in lane_metrics:
                continue
            for field in (
                "duration_ms",
                "input_tokens",
                "output_tokens",
                "human_actions",
                "control_plane_recoveries",
            ):
                try:
                    number = int(raw.get(field, 0) or 0)
                except (TypeError, ValueError):
                    raise ValueError("inspector telemetry %s must be an integer" % field)
                lane_metrics[lane][field] += max(0, number)
        for index, raw in enumerate(findings[:500]):
            if not isinstance(raw, Mapping):
                raise ValueError("finding entries must be objects")
            title = _text(raw.get("title"), "finding title", required=True, limit=500)
            lane = _text(raw.get("lane"), "finding lane", required=True, limit=100)
            severity = _text(raw.get("severity"), "finding severity", required=True, limit=30).lower()
            if severity not in SEVERITY_ORDER:
                raise ValueError("unknown finding severity: %s" % severity)
            confidence = _confidence(raw.get("confidence"))
            evidence = _text(raw.get("evidence"), "finding evidence", limit=8000)
            raw_reproduction = raw.get("reproduction")
            if isinstance(raw_reproduction, Mapping):
                reproduction = {
                    "preconditions": _strings(
                        raw_reproduction.get("preconditions"),
                        "reproduction preconditions",
                        limit=50,
                    ),
                    "steps": _strings(raw_reproduction.get("steps"), "reproduction steps", limit=100),
                    "expected": _text(raw_reproduction.get("expected"), "reproduction expected", limit=4000),
                    "actual": _text(raw_reproduction.get("actual"), "reproduction actual", limit=4000),
                }
                has_reproduction = bool(
                    reproduction["steps"] and reproduction["expected"] and reproduction["actual"]
                )
            else:
                reproduction = _text(raw_reproduction, "finding reproduction", limit=8000)
                has_reproduction = bool(reproduction)
            location = _text(raw.get("location"), "finding location", limit=1000)
            root_cause_key = _text(raw.get("root_cause_key"), "root cause key", limit=300)
            violated_invariant = _text(
                raw.get("violated_invariant"), "violated invariant", limit=2000
            )
            counterexample = _text(raw.get("counterexample"), "counterexample", limit=4000)
            artifact_refs = _strings(raw.get("artifact_refs"), "artifact_refs", limit=100)
            introduced = raw.get("introduced_by_change", "unknown")
            if isinstance(introduced, str):
                normalized_introduced = introduced.strip().lower()
                if normalized_introduced in {"true", "yes"}:
                    introduced_value: Any = True
                elif normalized_introduced in {"false", "no"}:
                    introduced_value = False
                else:
                    introduced_value = "unknown"
            else:
                introduced_value = bool(introduced)
            item = {
                "id": _text(raw.get("id"), "finding id", limit=120) or "finding-%d" % (index + 1),
                "lane": lane,
                "title": title,
                "severity": severity,
                "confidence": round(confidence, 4),
                "location": location,
                "evidence": evidence,
                "reproduction": reproduction,
                "root_cause_key": root_cause_key,
                "violated_invariant": violated_invariant,
                "counterexample": counterexample,
                "artifact_refs": artifact_refs,
                "introduced_by_change": introduced_value,
                "requires_human": bool(raw.get("requires_human", False)),
                "disputed": bool(raw.get("disputed", False)),
                "blocking_eligible": calibration_by_lane.get(lane, {}).get(
                    "mode", "blocking"
                )
                != "shadow",
                "enforcement_modes": [
                    calibration_by_lane.get(lane, {}).get("mode", "blocking")
                ],
            }
            if lane in lane_metrics:
                lane_metrics[lane]["submitted_findings"] += 1
            reason = ""
            if lane not in enabled_lanes:
                reason = "lane_not_enabled_by_plan"
            elif introduced_value is not True:
                reason = "introduced_by_change_unproven"
            elif confidence < 0.8:
                reason = "confidence_below_0.80"
            elif not evidence:
                reason = "missing_evidence"
            elif SEVERITY_ORDER[severity] >= SEVERITY_ORDER["high"] and not has_reproduction:
                reason = "high_severity_without_reproduction"
            if reason:
                rejected.append({**item, "rejected_reason": reason})
                if lane in lane_metrics:
                    lane_metrics[lane]["filtered_findings"] += 1
                    if bool(raw.get("confirmed_false_positive", False)):
                        lane_metrics[lane]["false_positives"] += 1
                continue
            key_source = root_cause_key.casefold() if root_cause_key else "%s|%s" % (
                re.sub(r"\s+", " ", title.casefold()), location.casefold()
            )
            key = hashlib.sha256(key_source.encode("utf-8")).hexdigest()[:16]
            item["fingerprint"] = key
            item["contributing_lanes"] = [lane]
            item["source_finding_ids"] = [item["id"]]
            previous = accepted_by_key.get(key)
            if previous is None:
                accepted_by_key[key] = item
            else:
                lanes = sorted(set(previous["contributing_lanes"] + [lane]))
                modes = sorted(
                    set(previous.get("enforcement_modes") or [])
                    | set(item.get("enforcement_modes") or [])
                )
                blocking_eligible = bool(
                    previous.get("blocking_eligible") or item.get("blocking_eligible")
                )
                merged_disputed = bool(previous.get("disputed") or item.get("disputed"))
                merged_requires_human = bool(
                    previous.get("requires_human") or item.get("requires_human")
                )
                source_ids = previous["source_finding_ids"] + [item["id"]]
                if (SEVERITY_ORDER[severity], confidence) > (
                    SEVERITY_ORDER[previous["severity"]], previous["confidence"]
                ):
                    item["contributing_lanes"] = lanes
                    item["source_finding_ids"] = source_ids
                    item["enforcement_modes"] = modes
                    item["blocking_eligible"] = blocking_eligible
                    item["disputed"] = merged_disputed
                    item["requires_human"] = merged_requires_human
                    accepted_by_key[key] = item
                else:
                    previous["contributing_lanes"] = lanes
                    previous["source_finding_ids"] = source_ids
                    previous["enforcement_modes"] = modes
                    previous["blocking_eligible"] = blocking_eligible
                    previous["disputed"] = merged_disputed
                    previous["requires_human"] = merged_requires_human

        accepted = list(accepted_by_key.values())
        accepted.sort(
            key=lambda item: (SEVERITY_ORDER[item["severity"]], item["confidence"]),
            reverse=True,
        )
        for item in accepted:
            for lane in item["contributing_lanes"]:
                if lane in lane_metrics:
                    lane_metrics[lane]["accepted_findings"] += 1
                    lane_metrics[lane]["independent_defect_contributions"] += 1
                    if len(item["contributing_lanes"]) == 1:
                        lane_metrics[lane]["unique_defect_contributions"] += 1
        semantic_blockers = [
            item
            for item in accepted
            if SEVERITY_ORDER[item["severity"]] >= SEVERITY_ORDER["high"]
            and item.get("blocking_eligible") is True
        ]
        shadow_findings = [
            item
            for item in accepted
            if SEVERITY_ORDER[item["severity"]] >= SEVERITY_ORDER["high"]
            and item.get("blocking_eligible") is not True
        ]
        warnings = [
            item
            for item in accepted
            if SEVERITY_ORDER[item["severity"]] < SEVERITY_ORDER["high"]
        ]
        disputed = any(
            item["disputed"] and item.get("blocking_eligible") is True
            for item in accepted
        )
        requires_human = any(
            item["requires_human"] and item.get("blocking_eligible") is True
            for item in accepted
        )
        has_blockers = bool(deterministic_blockers or semantic_blockers)

        if contract.get("status") != "ready":
            decision = "needs_clarification"
        elif disputed or requires_human:
            decision = "human_decision"
        elif has_blockers and repair_round < 1:
            decision = "repair_once"
        elif has_blockers:
            decision = "human_decision"
        else:
            decision = "ready_for_final_verification"

        return {
            "schema_version": CONTRACT_VERSION,
            "contract_id": contract["contract_id"],
            "contract_hash": contract["contract_hash"],
            "plan_hash": plan["plan_hash"],
            "decision": decision,
            "repair_round": repair_round,
            "deterministic_results": deterministic_summary,
            "deterministic_blockers": deterministic_blockers,
            "accepted_findings": accepted,
            "semantic_blockers": semantic_blockers,
            "shadow_findings": shadow_findings,
            "warnings": warnings,
            "rejected_findings": rejected,
            "repair_package": {
                "owner": "original_owner_context",
                "automatic_rounds_remaining": max(0, 1 - repair_round),
                "items": deterministic_blockers + semantic_blockers,
                "root_causes": [
                    {
                        "fingerprint": item["fingerprint"],
                        "title": item["title"],
                        "contributing_lanes": item["contributing_lanes"],
                        "source_finding_ids": item["source_finding_ids"],
                    }
                    for item in semantic_blockers
                ],
            },
            "metrics": {
                "submitted_findings": len(findings),
                "accepted_findings": len(accepted),
                "rejected_findings": len(rejected),
                "deduplicated_findings": max(0, len(findings) - len(rejected) - len(accepted)),
                "blocking_findings": len(deterministic_blockers) + len(semantic_blockers),
                "per_inspector": lane_metrics,
            },
        }

    def _validate_technology_research(self, research: Mapping[str, Any]) -> None:
        if not isinstance(research, Mapping):
            raise ValueError("technology research must be an object")
        if research.get("schema_version") != RESEARCH_VERSION:
            raise ValueError("unsupported technology research schema_version")
        source = {
            "research_question": copy.deepcopy(research.get("research_question")),
            "human_scope": copy.deepcopy(research.get("human_scope")),
            "as_of": research.get("as_of"),
            "queries": copy.deepcopy(research.get("queries")),
            "sources": copy.deepcopy(research.get("sources")),
            "framework_candidates": copy.deepcopy(
                research.get("framework_candidates")
            ),
            "technology_paths": copy.deepcopy(research.get("technology_paths")),
            "recommendation": copy.deepcopy(research.get("recommendation")),
            "review_declaration": copy.deepcopy(research.get("review_declaration")),
            "review_findings": copy.deepcopy(research.get("review_findings")),
            "review_verdict": research.get("requested_verdict"),
        }
        expected = self.compile_technology_research(source)
        for field, expected_value in expected.items():
            if research.get(field) != expected_value:
                raise ValueError(
                    "technology research integrity check failed for %s" % field
                )

    def _validate_intent_brief(self, brief: Mapping[str, Any]) -> None:
        if not isinstance(brief, Mapping):
            raise ValueError("intent brief must be an object")
        if brief.get("schema_version") != INTENT_VERSION:
            raise ValueError("unsupported intent brief schema_version")
        source_fields = (
            "original_request",
            "conversation_refs",
            "expected_outcomes",
            "acceptance_examples",
            "development_executor",
            "product_runtime",
            "technical_choices",
            "technology_research",
            "technology_strategy",
            "non_goals",
            "risk_boundaries",
            "research_refs",
            "unresolved_questions",
        )
        expected = self.compile_intent_brief(
            {field: copy.deepcopy(brief.get(field)) for field in source_fields}
        )
        for field, expected_value in expected.items():
            if brief.get(field) != expected_value:
                raise ValueError("intent brief integrity check failed for %s" % field)

    def _validate_intent_inspection(self, inspection: Mapping[str, Any]) -> None:
        if not isinstance(inspection, Mapping):
            raise ValueError("intent inspection must be an object")
        if inspection.get("schema_version") != INTENT_VERSION:
            raise ValueError("unsupported intent inspection schema_version")
        source = {
            "brief": copy.deepcopy(inspection.get("brief")),
            "proposed_contract_source": copy.deepcopy(
                inspection.get("proposed_contract_source")
            ),
            "technology_research": copy.deepcopy(
                inspection.get("technology_research")
            ),
            "research_evidence": copy.deepcopy(inspection.get("research_evidence")),
            "evidence_inputs": copy.deepcopy(inspection.get("evidence_inputs")),
            "inspector_declaration": copy.deepcopy(
                inspection.get("inspector_declaration")
            ),
            "coverage": copy.deepcopy(inspection.get("coverage")),
            "findings": copy.deepcopy(inspection.get("findings")),
            "verdict": inspection.get("requested_verdict"),
        }
        expected = self.compile_intent_inspection(source)
        for field, expected_value in expected.items():
            if inspection.get(field) != expected_value:
                raise ValueError(
                    "intent inspection integrity check failed for %s" % field
                )

    def _validate_contract(self, contract: Mapping[str, Any]) -> None:
        if not isinstance(contract, Mapping):
            raise ValueError("contract must be an object")
        if contract.get("schema_version") != CONTRACT_VERSION:
            raise ValueError("unsupported contract schema_version")
        if not contract.get("contract_id") or not contract.get("contract_hash"):
            raise ValueError("compiled contract id and hash are required")
        source_fields = (
            "title",
            "goal",
            "users",
            "outcomes",
            "acceptance_criteria",
            "non_goals",
            "constraints",
            "forbidden_behaviors",
            "human_decisions",
            "deterministic_checks",
            "change_types",
            "intent_alignment",
            "environment",
            "required_evidence_classes",
            "must_kill_cases",
        )
        source = {field: contract.get(field) for field in source_fields}
        source["risk_flags"] = contract.get("explicit_risk_flags")
        source["uncertainties"] = contract.get("uncertainty_registry")
        expected = self.compile_contract(source)
        for field, expected_value in expected.items():
            if contract.get(field) != expected_value:
                raise ValueError(
                    "compiled contract integrity check failed for %s" % field
                )

    def _validate_plan(
        self, contract: Mapping[str, Any], plan: Mapping[str, Any]
    ) -> None:
        self._validate_contract(contract)
        if not isinstance(plan, Mapping):
            raise ValueError("plan must be an object")
        if plan.get("contract_hash") != contract.get("contract_hash"):
            raise ValueError("plan does not belong to this contract hash")
        expected = self.route(contract, plan.get("bad_case_registry"))
        for field, expected_value in expected.items():
            if plan.get(field) != expected_value:
                raise ValueError("verification plan integrity check failed for %s" % field)


def integration_blueprint() -> Dict[str, Any]:
    """Describe ownership boundaries without requiring any external service."""
    return {
        "schema_version": CONTRACT_VERSION,
        "principle": "Reuse mature control planes; keep MCP decisions stateless and runtime checkpoints narrow, signed, and caller-owned.",
        "components": [
            {
                "name": "LobeHub",
                "responsibility": "human front door, conversation, projects, approvals, and reports",
                "interface": "released MCP/Skill/CLI interfaces",
                "source_of_truth": ["human intent", "human decisions", "operator-visible task state"],
                "must_not_duplicate": ["task board", "chat UI", "identity", "memory"],
            },
            {
                "name": "Kandev",
                "responsibility": "development workbench, coding-agent sessions, worktrees, diff and review",
                "interface": "Kandev MCP and terminal-native CLI adapters",
                "source_of_truth": ["coding task execution", "worktree and branch state"],
                "must_not_duplicate": ["worktree manager", "terminal console", "code review UI"],
            },
            {
                "name": "OpenAI Symphony",
                "responsibility": "long-running ticket dispatch semantics, retries, reconciliation and WORKFLOW.md",
                "interface": "Codex app-server and repository-owned WORKFLOW.md",
                "source_of_truth": ["runner attempt state when Symphony is selected"],
                "must_not_duplicate": ["scheduler retry protocol", "workspace reconciliation"],
            },
            {
                "name": "GitHub Spec Kit",
                "responsibility": "constitution, specification, clarification, plan and task artifacts",
                "interface": "repository files and Codex Skills",
                "source_of_truth": ["requirements and planning artifacts"],
                "must_not_duplicate": ["specification authoring workflow"],
            },
            {
                "name": "GitHub Actions / gh-aw",
                "responsibility": "deterministic CI, repository events, read-only agent jobs and safe outputs",
                "interface": "workflow files and artifacts",
                "source_of_truth": ["CI execution evidence", "PR checks"],
                "must_not_duplicate": ["CI runner", "GitHub permissions layer"],
            },
            {
                "name": "AI Delivery Governance",
                "responsibility": "technology-research evidence compilation, human-selected bounded-race policy, contract validation, uncertainty routing, environment/invariant protocol, isolated verification and adjudication",
                "interface": "stateless API/CLI/MCP plus a caller-owned signed ledger library",
                "source_of_truth": ["versioned policy only"],
                "must_not_own": ["tasks", "agent sessions", "branches", "approvals", "credentials"],
            },
        ],
        "default_path": {
            "human_surface": "LobeHub",
            "development_control_plane": "Kandev",
            "long_running_runner_semantics": "Symphony WORKFLOW.md",
            "requirements": "Spec Kit artifacts compiled into a work contract",
            "technology_selection": "four-channel research, fresh read-only quality inspection, then human single-path or bounded 2-3 path race choice",
            "executor": "locally authenticated Codex CLI/app-server",
            "verification": "deterministic CI, three orthogonal fresh contexts, risk extensions, full rerun and blind final verification",
        },
        "prohibited": [
            "model API keys",
            "automatic merge or release",
            "inspectors editing product code",
            "a second task database inside the governance layer",
            "claiming an estimated quota is exact provider telemetry",
        ],
    }
