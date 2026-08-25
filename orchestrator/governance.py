"""Deterministic governance for AI-native software delivery.

This module deliberately does not run an agent, own task state, or call a model.
It compiles a human-owned work contract, selects independent verification lanes,
and adjudicates structured evidence.  LobeHub, Kandev, Symphony, Codex, or any
other runtime can consume the same policy without becoming a second source of
truth.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


CONTRACT_VERSION = "1.0"
PLAN_VERSION = "1.0"
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

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
        return {
            "id": self.lane_id,
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

        clarifications: List[Dict[str, str]] = []
        if not users:
            clarifications.append(
                {
                    "id": "target_users",
                    "question": "谁会在什么场景下使用这项交付？",
                    "reason": "没有用户场景时，功能测试容易退化为实现自证。",
                }
            )
        if not outcomes:
            clarifications.append(
                {
                    "id": "observable_outcome",
                    "question": "完成后，哪个可观察结果能证明目标已经实现？",
                    "reason": "目标需要独立于实现方式的成功信号。",
                }
            )
        if not acceptance:
            clarifications.append(
                {
                    "id": "acceptance_criteria",
                    "question": "请至少给出一条可验证的验收标准。",
                    "reason": "没有验收标准时，执行 Agent 同时定义问题和成功。",
                }
            )
        if not non_goals:
            clarifications.append(
                {
                    "id": "non_goals",
                    "question": "本次明确不做什么，哪些相邻改进应留到以后？",
                    "reason": "非目标用于阻止执行过程中的隐性扩张。",
                }
            )
        if not deterministic_checks:
            clarifications.append(
                {
                    "id": "deterministic_evidence",
                    "question": "哪些确定性命令或检查必须通过，才能证明本次交付完成？",
                    "reason": "没有预先声明的确定性证据时，完成结论会退化为 Agent 自证。",
                }
            )
        if (set(risk_flags) & (CRITICAL_FLAGS | HIGH_FLAGS)) and not human_decisions:
            clarifications.append(
                {
                    "id": "human_decisions",
                    "question": "高风险事项中哪些选择和最终动作必须由人确认？",
                    "reason": "安全、数据、计费和不可逆动作不能由执行者自行授权。",
                }
            )

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
            "change_control": {
                "rule": "Any change to immutable contract fields creates a new contract hash.",
                "decision_log_required": True,
            },
        }

    def route(self, contract: Mapping[str, Any]) -> Dict[str, Any]:
        self._validate_contract(contract)
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
                        "requirement-conformance",
                        "semantic",
                        "Map every acceptance criterion and forbidden behavior to observable evidence.",
                        ("contract", "diff", "deterministic_results"),
                        "A missing core criterion with file/runtime evidence blocks.",
                        "Non-trivial changes require an oracle independent of the owner.",
                    ),
                    LaneDefinition(
                        "code-architecture",
                        "semantic",
                        "Check repository rules, invariants, compatibility, error handling, and blast radius.",
                        ("contract", "diff", "project_rules", "git_history"),
                        "Only reproducible high-confidence defects or explicit rule violations block.",
                        "Code quality means architecture and invariants, not stylistic opinion.",
                    ),
                    LaneDefinition(
                        "test-quality",
                        "semantic",
                        "Falsify the tests: weak assertions, missing boundaries, false mocks, and untested failures.",
                        ("contract", "diff", "tests", "deterministic_results"),
                        "Tests that cannot fail under a material implementation fault block completion claims.",
                        "Test generation and test judgment must use isolated contexts.",
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
                "contract",
                "owner_implementation",
                "deterministic_ci",
                "independent_inspection",
                "adjudication",
                "single_consolidated_repair",
                "full_reverification",
                "human_gate",
            ],
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
                    "contract_id": contract["contract_id"],
                    "contract_hash": contract["contract_hash"],
                    "goal": contract["goal"],
                    "acceptance_criteria": contract["acceptance_criteria"],
                    "non_goals": contract["non_goals"],
                    "constraints": contract["constraints"],
                    "forbidden_behaviors": contract["forbidden_behaviors"],
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
                            "title",
                            "severity",
                            "confidence",
                            "evidence",
                            "reproduction",
                            "introduced_by_change",
                        ],
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
                "schema_version": "1.0",
                "contract_id": contract["contract_id"],
                "contract_hash": contract["contract_hash"],
                "plan_hash": plan["plan_hash"],
                "status": "blocked_on_clarification",
                "next_action": "return_to_human_intake",
                "clarifications": contract.get("clarifications") or [],
                "external_actions_allowed": False,
            }

        contexts = self.context_packets(contract, plan)
        owner_instructions = [
            "Treat the compiled contract and its hash as immutable for this attempt.",
            "Use one continuous Codex session for investigation, implementation, and at most one consolidated repair.",
            "Work only in the assigned isolated worktree and keep changes inside the stated goal and non-goals.",
            "Run every declared deterministic check and preserve its command, status, and evidence.",
            "Do not push, merge, deploy, publish, contact external people, purchase usage, consume reset credits, or accept an API key.",
            "Stop for a human on ambiguity, policy, taste, permission expansion, or an irreversible/external action.",
        ]
        inspectors = []
        for context in contexts:
            inspectors.append(
                {
                    "title": ("Inspect: %s" % context["lane_id"])[:60],
                    "lane_id": context["lane_id"],
                    "activation": "after_all_required_deterministic_checks_pass",
                    "session": "new_task_and_fresh_session",
                    "profile_requirement": "Codex inspector profile with repository read-only and no external writes",
                    "context": context,
                }
            )

        return {
            "schema_version": "1.0",
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
            "owner_task": {
                "title": ("Deliver: %s" % contract["title"])[:60],
                "session": "continuous_until_handoff_or_single_repair",
                "contract": contract,
                "instructions": owner_instructions,
                "required_output": {
                    "diff": True,
                    "deterministic_results": contract["deterministic_checks"],
                    "decision_log": True,
                    "external_actions_performed": [],
                },
            },
            "inspector_tasks": inspectors,
            "sequence": plan["sequence"],
            "adjudication": {
                "tool": "adjudicate_delivery",
                "repair_owner": "original_owner_context",
                "max_automatic_rounds": 1,
                "successful_handoff": "ready_for_human_merge",
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
        if not isinstance(deterministic_results, list) or not isinstance(findings, list):
            raise ValueError("deterministic_results and findings must be arrays")

        deterministic_blockers: List[Dict[str, Any]] = []
        deterministic_summary: List[Dict[str, Any]] = []
        observed_checks = set()
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
            }
            deterministic_summary.append(item)
            observed_checks.update(
                value.casefold()
                for value in (item["name"], item["command"])
                if value
            )
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

        accepted_by_key: Dict[str, Dict[str, Any]] = {}
        rejected: List[Dict[str, Any]] = []
        disputed = False
        enabled_lanes = {
            str(item.get("id"))
            for item in plan.get("lanes") or []
            if item.get("kind") != "deterministic"
        }
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
            reproduction = _text(raw.get("reproduction"), "finding reproduction", limit=8000)
            location = _text(raw.get("location"), "finding location", limit=1000)
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
                "introduced_by_change": introduced_value,
                "requires_human": bool(raw.get("requires_human", False)),
                "disputed": bool(raw.get("disputed", False)),
            }
            reason = ""
            if lane not in enabled_lanes:
                reason = "lane_not_enabled_by_plan"
            elif introduced_value is not True:
                reason = "introduced_by_change_unproven"
            elif confidence < 0.8:
                reason = "confidence_below_0.80"
            elif not evidence:
                reason = "missing_evidence"
            elif SEVERITY_ORDER[severity] >= SEVERITY_ORDER["high"] and not reproduction:
                reason = "high_severity_without_reproduction"
            if reason:
                rejected.append({**item, "rejected_reason": reason})
                continue
            disputed = disputed or item["disputed"]
            key_source = "%s|%s" % (
                re.sub(r"\s+", " ", title.casefold()),
                location.casefold(),
            )
            key = hashlib.sha256(key_source.encode("utf-8")).hexdigest()[:16]
            item["fingerprint"] = key
            previous = accepted_by_key.get(key)
            if previous is None or (
                SEVERITY_ORDER[severity], confidence
            ) > (
                SEVERITY_ORDER[previous["severity"]], previous["confidence"]
            ):
                accepted_by_key[key] = item

        accepted = list(accepted_by_key.values())
        accepted.sort(
            key=lambda item: (SEVERITY_ORDER[item["severity"]], item["confidence"]),
            reverse=True,
        )
        semantic_blockers = [
            item
            for item in accepted
            if SEVERITY_ORDER[item["severity"]] >= SEVERITY_ORDER["high"]
        ]
        warnings = [
            item
            for item in accepted
            if SEVERITY_ORDER[item["severity"]] < SEVERITY_ORDER["high"]
        ]
        requires_human = any(item["requires_human"] for item in accepted)
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
            decision = "ready_for_human_merge"

        return {
            "schema_version": "1.0",
            "contract_id": contract["contract_id"],
            "contract_hash": contract["contract_hash"],
            "decision": decision,
            "repair_round": repair_round,
            "deterministic_results": deterministic_summary,
            "deterministic_blockers": deterministic_blockers,
            "accepted_findings": accepted,
            "semantic_blockers": semantic_blockers,
            "warnings": warnings,
            "rejected_findings": rejected,
            "repair_package": {
                "owner": "original_owner_context",
                "automatic_rounds_remaining": max(0, 1 - repair_round),
                "items": deterministic_blockers + semantic_blockers,
            },
            "metrics": {
                "submitted_findings": len(findings),
                "accepted_findings": len(accepted),
                "rejected_findings": len(rejected),
                "deduplicated_findings": max(0, len(findings) - len(rejected) - len(accepted)),
                "blocking_findings": len(deterministic_blockers) + len(semantic_blockers),
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
        )
        source = {field: contract.get(field) for field in source_fields}
        source["risk_flags"] = contract.get("explicit_risk_flags")
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
        expected = self.route(contract)
        for field, expected_value in expected.items():
            if plan.get(field) != expected_value:
                raise ValueError("verification plan integrity check failed for %s" % field)


def integration_blueprint() -> Dict[str, Any]:
    """Describe ownership boundaries without requiring any external service."""
    return {
        "schema_version": "1.0",
        "principle": "Reuse mature control planes; keep this package stateless and policy-focused.",
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
                "responsibility": "contract validation, risk routing, isolated verification policy and adjudication",
                "interface": "stateless Python API, CLI and MCP",
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
            "verification": "deterministic CI followed by risk-selected fresh contexts",
        },
        "prohibited": [
            "model API keys",
            "automatic merge or release",
            "inspectors editing product code",
            "a second task database inside the governance layer",
            "claiming an estimated quota is exact provider telemetry",
        ],
    }
