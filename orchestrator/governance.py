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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .governance_learning import (
    calibration_modes,
    validate_bad_case_registry,
)


CONTRACT_VERSION = "2.1"
PLAN_VERSION = "2.1"
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

        clarifications: List[Dict[str, Any]] = []
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
                "schema_version": "2.1",
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
            "schema_version": "2.1",
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

        human_gates = ["final_merge_or_release"]
        if contract.get("status") != "ready":
            human_gates.insert(0, "contract_clarification")
        if RISK_ORDER[level] >= RISK_ORDER["high"]:
            human_gates.insert(-1, "high_risk_policy_and_release")
        if "user_experience" in flags:
            human_gates.insert(-1, "experience_and_taste")

        plan = {
            "schema_version": PLAN_VERSION,
            "contract_id": contract["contract_id"],
            "contract_hash": contract["contract_hash"],
            "risk_level": level,
            "execution": {
                "owner_context": "continuous",
                "owner_write_access": True,
                "selection": "single owner by default; fan out only the enabled independent lanes",
                "max_parallel_inspectors": min(4, max(1, len(lanes) - 1)),
            },
            "sequence": [
                "environment_preflight",
                "owner_implementation",
                "deterministic_ci",
                "independent_inspection",
                "adjudication",
                "single_consolidated_repair",
                "full_reverification",
                "blind_final_verification",
                "human_handoff",
            ],
            "required_checks": list(contract.get("deterministic_checks") or []),
            "required_evidence_classes": list(
                contract.get("required_evidence_classes") or []
            ),
            "must_kill_cases": all_must_kill_cases,
            "bad_case_registry": compiled_registry,
            "hidden_case_ids": [item["id"] for item in hidden_cases],
            "artifact_invariants": {
                "environment_preflight": [
                    "environment capsule is ready and bound to the contract",
                    "assigned repository root equals the real Git diff root",
                    "required commands, ports, permissions, and locks are healthy",
                ],
                "owner_implementation": [
                    "owner context id and real diff digest exist",
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
            },
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
                "schema_version": "2.1",
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

        contexts = self.context_packets(contract, plan)
        owner_instructions = [
            "Treat the compiled contract and its hash as immutable for this attempt.",
            "Do not begin until the environment capsule is ready and proves the assigned repository is the real diff root.",
            "Use one continuous Codex session for investigation, implementation, and at most one consolidated repair.",
            "Work only in the assigned isolated worktree and keep changes inside the stated goal and non-goals.",
            "Run every declared deterministic check and preserve its command, status, and evidence.",
            "Do not push, merge, deploy, publish, contact external people, purchase usage, consume reset credits, or accept an API key.",
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

        return {
            "schema_version": "2.1",
            "contract_id": contract["contract_id"],
            "contract_hash": contract["contract_hash"],
            "plan_hash": plan["plan_hash"],
            "status": "ready_for_control_plane",
            "target": "Kandev",
            "executor": {
                "agent": "Codex",
                "authentication": "locally authenticated ChatGPT subscription under the Kandev service user",
                "api_key_allowed": False,
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
                    "execution_state": "queued",
                    "progress_summary": "等待环境预检",
                    "current_difficulty": "无；尚未启动",
                    "last_heartbeat_at": "",
                    "needs_human": False,
                    "dependency": "environment_preflight",
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
                "task_creation": "create the owner first; create only the listed inspector tasks after deterministic CI passes",
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
            "schema_version": "2.1",
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
        "schema_version": "2.1",
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
                "responsibility": "contract validation, uncertainty routing, environment/invariant protocol, isolated verification and adjudication",
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
