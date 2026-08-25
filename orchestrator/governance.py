"""Policy primitives layered on top of LobeHub's native task runtime.

LobeHub owns projects, tasks, topics, execution, verification rounds and the
human review UI.  This module deliberately owns none of that state.  It only
turns the operating principles of this project into deterministic advice that
LobeHub agents (including the local Codex harness) can consume.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

from .quality import build_verification_plan, effective_risk, normalize_surfaces, string_list


RISK_LEVELS = ("low", "medium", "high", "critical")
PURPOSES = ("delivery", "repair", "research", "adversarial_review")


def _tokens(value: Any) -> Set[str]:
    text = str(value or "").lower()
    # Keep repository paths/identifiers intact and split Chinese text into
    # characters.  The router is an explainable affinity heuristic, not an
    # embedding model and therefore never needs a model API.
    return set(re.findall(r"[a-z0-9_./-]+|[\u3400-\u9fff]", text))


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    first, second = set(left), set(right)
    union = first | second
    return len(first & second) / float(len(union)) if union else 0.0


def _criterion_texts(values: Sequence[Any]) -> List[str]:
    result: List[str] = []
    for value in values:
        if isinstance(value, dict):
            text = str(value.get("title") or value.get("criterion") or "").strip()
        else:
            text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _deterministic_only_criterion(value: str) -> bool:
    """Return True when a purported acceptance check is only a CI precondition."""

    normalized = re.sub(r"\s+", " ", value.strip().lower())
    patterns = (
        r"^(all )?tests? pass(ed)?[.!]?$",
        r"^(ci|build|lint|type ?checks?) pass(es|ed)?[.!]?$",
        r"^no (lint|type) errors?[.!]?$",
        r"^(pytest|unittest|npm test|cargo test|go test) pass(es|ed)?[.!]?$",
    )
    return any(re.match(pattern, normalized) for pattern in patterns)


def _contract_digest(contract: Dict[str, Any]) -> str:
    canonical = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compile_work_contract(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Compile one human goal into a frozen, evidence-oriented work contract.

    Missing critical outcome evidence is reported as ``needs_clarification``.
    The compiler never invents acceptance criteria merely to let work start.
    """

    goal = str(arguments.get("goal", "")).strip()
    if not goal:
        raise ValueError("goal is required")
    risk = str(arguments.get("risk", "medium")).strip().lower()
    if risk not in RISK_LEVELS:
        raise ValueError("risk must be one of: %s" % ", ".join(RISK_LEVELS))

    subjective = bool(arguments.get("subjective", False))
    irreversible = bool(arguments.get("irreversible", False))
    external_effects = bool(arguments.get("external_effects", False))
    security_sensitive = bool(arguments.get("security_sensitive", False))
    criteria = _criterion_texts(arguments.get("acceptance_criteria") or [])
    user_outcome = str(arguments.get("user_outcome") or goal).strip()
    non_goals = string_list(arguments.get("non_goals"))
    prohibited = string_list(arguments.get("prohibited_behaviors"))
    assumptions = string_list(arguments.get("assumptions"))
    constraints = string_list(arguments.get("constraints"))
    change_surfaces = normalize_surfaces(arguments.get("change_surfaces"))
    security_boundaries = string_list(arguments.get("security_boundaries"))
    rollback_plan = str(arguments.get("rollback_plan") or "").strip()
    observability_signals = string_list(arguments.get("observability_signals"))
    performance_budgets = string_list(arguments.get("performance_budgets"))

    risk_input = dict(arguments)
    risk_input["risk"] = risk
    risk_input["change_surfaces"] = change_surfaces
    assessed_risk = effective_risk(risk_input)
    independent_falsification = RISK_LEVELS.index(assessed_risk) >= 1
    immediate_human_gate = (
        RISK_LEVELS.index(assessed_risk) >= 2
        or subjective
        or irreversible
        or external_effects
        or security_sensitive
    )

    clarification_questions: List[Dict[str, str]] = []
    if not criteria:
        clarification_questions.append(
            {
                "field": "acceptance_criteria",
                "question": "哪些用户或操作员可观察的结果，能够证明这项工作已经完成？",
                "reason": "没有可验证的结果边界，Agent 容易把‘做了改动’误当成‘交付成功’。",
            }
        )
    deterministic_only = [item for item in criteria if _deterministic_only_criterion(item)]
    if criteria and len(deterministic_only) == len(criteria):
        clarification_questions.append(
            {
                "field": "acceptance_criteria",
                "question": "除了测试或 CI 通过，用户最终应该观察到什么行为或结果？",
                "reason": "测试、构建、Lint 和类型检查是发布前置门禁，不是用户验收结果。",
            }
        )
    if security_sensitive and not security_boundaries:
        clarification_questions.append(
            {
                "field": "security_boundaries",
                "question": "本次变更涉及哪些信任边界、敏感数据和权限边界？",
                "reason": "安全敏感工作必须先冻结保护对象与攻击面。",
            }
        )
    if (irreversible or external_effects) and not rollback_plan:
        clarification_questions.append(
            {
                "field": "rollback_plan",
                "question": "外部影响或不可逆操作失败时，如何回滚或补偿？",
                "reason": "没有恢复路径就不能自动进入有副作用的执行。",
            }
        )
    if RISK_LEVELS.index(assessed_risk) >= 2 and not observability_signals:
        clarification_questions.append(
            {
                "field": "observability_signals",
                "question": "上线或运行后，用哪些信号判断成功、失败、成本和恢复状态？",
                "reason": "高风险交付必须能在不读源码的情况下诊断。",
            }
        )

    status = "needs_clarification" if clarification_questions else "ready"

    gates: List[Dict[str, Any]] = [
        {
            "kind": "program",
            "blocking": True,
            "on_failure": "auto_repair_once",
            "purpose": "Run repository-defined tests, lint, type checks and artifact checks.",
        }
    ]
    if independent_falsification:
        gates.append(
            {
                "kind": "agent",
                "blocking": True,
                "fresh_context": True,
                "on_failure": "return_specific_counterexample",
                "purpose": "Try to disprove the delivery against the goal and acceptance criteria.",
            }
        )
    gates.append(
        {
            "kind": "human",
            "blocking": True,
            "when": (
                "before execution and final acceptance"
                if immediate_human_gate
                else "only on disagreement, ambiguity, or final merge/release"
            ),
            "purpose": "Direction, taste, product experience and accountable final decision.",
        }
    )

    contract: Dict[str, Any] = {
        "contract_version": "2.0",
        "status": status,
        "goal": goal,
        "user_outcome": user_outcome,
        "non_goals": non_goals,
        "prohibited_behaviors": prohibited,
        "assumptions": assumptions,
        "constraints": constraints,
        "change_surfaces": change_surfaces,
        "security_boundaries": security_boundaries,
        "rollback_plan": rollback_plan,
        "observability_signals": observability_signals,
        "performance_budgets": performance_budgets,
        "risk": assessed_risk,
        "requested_risk": risk,
        "subjective": subjective,
        "irreversible": irreversible,
        "external_effects": external_effects,
        "security_sensitive": security_sensitive,
        "acceptance_criteria": criteria,
        "clarification_questions": clarification_questions,
        "execution_policy": {
            "default_owner": "one_continuous_context",
            "continue_existing_topic": True,
            "new_context_only_for": [
                "genuinely independent parallel work",
                "adversarial falsification",
                "incompatible or contaminated context",
            ],
            "human_role_simulation": False,
            "max_automatic_repair_rounds": 1,
            "disagreement_action": "escalate_to_human_decision_inbox",
        },
        "lobe_settings": {
            "checkpoint_on_agent_request": True,
            "pause_before_each_topic": immediate_human_gate,
            "pause_after_each_topic": immediate_human_gate,
            "max_repair_rounds": 1,
        },
        "verification_gates": gates,
        "requires_immediate_human_decision": immediate_human_gate,
    }
    contract["verification_plan"] = build_verification_plan({"contract": contract})
    digest_input = dict(contract)
    contract["contract_hash"] = _contract_digest(digest_input)
    contract["task_instruction"] = _operator_instruction(contract)
    return contract


def _section(title: str, values: Sequence[str], empty: str = "- None declared") -> str:
    return "%s\n%s" % (title, "\n".join("- %s" % item for item in values) or empty)


def _operator_instruction(contract: Dict[str, Any]) -> str:
    checklist = "\n".join("- %s" % item for item in contract["acceptance_criteria"])
    if not checklist:
        checklist = "- UNRESOLVED: obtain observable acceptance criteria before implementation."
    independent_falsification = any(
        lane.get("fresh_context")
        for lane in contract["verification_plan"]["independent_lanes"]
    )
    review = (
        "After deterministic checks pass, request a fresh-context adversarial check. "
        "The checker must search for counterexamples and must not edit the delivery."
        if independent_falsification
        else "Do not create a separate reviewer unless deterministic evidence disagrees or risk changes."
    )
    human = (
        "Pause for a human decision before material execution and again before final acceptance."
        if contract["requires_immediate_human_decision"]
        else "Continue automatically while deterministic evidence and the independent check agree; "
        "escalate ambiguity, disagreement, taste, and final merge/release decisions to a human."
    )
    clarification = ""
    if contract["clarification_questions"]:
        clarification = "\n\nCLARIFICATION REQUIRED\n" + "\n".join(
            "- %s" % item["question"] for item in contract["clarification_questions"]
        )
    return """WORK CONTRACT v%s (%s)
Contract hash: %s

GOAL
%s

USER OUTCOME
%s

ACCEPTANCE CRITERIA
%s

%s

%s

%s

%s

CHANGE SURFACES
%s

ROLLBACK / COMPENSATING ACTION
%s

OBSERVABILITY SIGNALS
%s

PERFORMANCE BUDGETS
%s%s

OPERATING CONTRACT
- Risk: %s.
- If status is needs_clarification, do not make material product changes; ask only the listed blocking questions.
- Keep one continuous owner topic from investigation through implementation and repair.
- Continue the most context-compatible existing topic. Do not create project-manager,
  developer, tester, or QA personas merely to imitate a human org chart.
- Create another topic only for truly independent parallel work, adversarial falsification,
  or an incompatible/contaminated context.
- Prefer programmatic checks over model judgment. Record commands, exits and artifacts.
- Program checks are preconditions, never substitutes for observable Acceptance evidence.
- Independent verification lanes are read-only and must not edit the delivery.
- Report only evidence-backed findings with confidence >= 80; batch them into one repair brief.
- Allow at most one consolidated automatic repair round. If it still fails, stop and escalate.
- %s
- %s
- Never push, merge, deploy, publish, purchase usage, or bypass a permission boundary.
- Use the locally authenticated Codex/other CLI harness; do not request an API key.
""" % (
        contract["contract_version"],
        contract["status"],
        contract["contract_hash"],
        contract["goal"],
        contract["user_outcome"],
        checklist,
        _section("NON-GOALS", contract["non_goals"]),
        _section("PROHIBITED BEHAVIORS", contract["prohibited_behaviors"]),
        _section("ASSUMPTIONS TO VALIDATE", contract["assumptions"]),
        _section("CONSTRAINTS", contract["constraints"]),
        "\n".join("- %s" % item for item in contract["change_surfaces"]) or "- None declared",
        contract["rollback_plan"] or "Not required or not yet declared.",
        "\n".join("- %s" % item for item in contract["observability_signals"]) or "- None declared",
        "\n".join("- %s" % item for item in contract["performance_budgets"]) or "- None declared",
        clarification,
        contract["risk"],
        review,
        human,
    )


def route_context(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Choose a LobeHub topic by affinity, or explain why a fresh one is safer."""

    purpose = str(arguments.get("purpose", "delivery")).strip().lower()
    if purpose not in PURPOSES:
        raise ValueError("purpose must be one of: %s" % ", ".join(PURPOSES))
    work = str(arguments.get("work", "")).strip()
    if not work:
        raise ValueError("work is required")
    candidates = arguments.get("candidates") or []
    if not isinstance(candidates, list):
        raise ValueError("candidates must be an array")

    if purpose == "adversarial_review":
        return {
            "decision": "new_context",
            "score": 0.0,
            "reason": "Adversarial falsification must not inherit the author's topic context.",
        }
    if not candidates:
        return {
            "decision": "new_context",
            "score": 0.0,
            "reason": "No existing LobeHub topic was supplied as a candidate.",
        }

    project_id = str(arguments.get("project_id", "")).strip()
    repo_path = str(arguments.get("repo_path", "")).strip()
    paths = set(str(item) for item in (arguments.get("touched_paths") or []) if str(item))
    work_tokens = _tokens(work)
    ranked: List[Tuple[float, Dict[str, Any], List[str]]] = []

    for raw in candidates[:100]:
        if not isinstance(raw, dict):
            continue
        score = 0.0
        reasons: List[str] = []
        if project_id and str(raw.get("project_id", "")) == project_id:
            score += 0.28
            reasons.append("same project")
        if repo_path and str(raw.get("repo_path", "")) == repo_path:
            score += 0.22
            reasons.append("same repository")
        if str(raw.get("status", "")).lower() in ("active", "running", "paused", "completed"):
            score += 0.08
            reasons.append("usable topic state")
        candidate_paths = set(
            str(item) for item in (raw.get("touched_paths") or []) if str(item)
        )
        path_overlap = _jaccard(paths, candidate_paths)
        if path_overlap:
            score += 0.24 * path_overlap
            reasons.append("shared paths %.0f%%" % (path_overlap * 100))
        summary_tokens = _tokens(
            "%s %s" % (raw.get("title", ""), raw.get("summary", ""))
        )
        semantic_overlap = _jaccard(work_tokens, summary_tokens)
        if semantic_overlap:
            score += 0.24 * semantic_overlap
            reasons.append("shared concepts %.0f%%" % (semantic_overlap * 100))
        if raw.get("contaminated") or raw.get("incompatible"):
            score -= 0.6
            reasons.append("context marked incompatible")
        ranked.append((max(0.0, min(1.0, score)), raw, reasons))

    if not ranked:
        return {
            "decision": "new_context",
            "score": 0.0,
            "reason": "No valid context candidate was supplied.",
        }
    ranked.sort(key=lambda item: item[0], reverse=True)
    score, winner, reasons = ranked[0]
    threshold = 0.62 if purpose == "research" else 0.52
    context_id = str(winner.get("topic_id") or winner.get("id") or "").strip()
    if score >= threshold and context_id:
        session_id = str(
            winner.get("codex_session_id") or winner.get("session_id") or ""
        ).strip()
        resume_value = session_id or "<CodexSessionId>"
        return {
            "decision": "continue_existing",
            "topic_id": context_id,
            "codex_session_id": session_id or None,
            "score": round(score, 3),
            "reason": "; ".join(reasons) or "highest context affinity",
            "orchestrator_cli_hint": (
                "python3 run.py execute-topic --task <task> --topic %s "
                "--repo <repo> --sandbox workspace-write --resume %s"
            )
            % (context_id, resume_value),
        }
    return {
        "decision": "new_context",
        "score": round(score, 3),
        "best_candidate": context_id or None,
        "reason": "Best affinity %.3f is below the %.2f continuity threshold."
        % (score, threshold),
    }
