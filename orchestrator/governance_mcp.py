"""Stateless MCP policy tools intended for LobeHub Desktop."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Dict, List, Optional, TextIO

from .governance import RISK_LEVELS, compile_work_contract, route_context
from .lobehub import codex_binary
from .quality import (
    CHANGE_SURFACES,
    aggregate_findings,
    build_verification_plan,
    compare_contracts,
    decide_release_readiness,
)
from .quota import CodexQuotaProbe, choose_model_tier, decision_reason, quota_mode


PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOLS = (PROTOCOL_VERSION, "2025-03-26", "2024-11-05")


def quota_advice(arguments: Dict[str, Any]) -> Dict[str, Any]:
    role = str(arguments.get("work_type", "implementer")).strip() or "implementer"
    priority = int(arguments.get("priority", 50) or 50)
    snapshot = CodexQuotaProbe(codex_binary(), ttl_seconds=5).read(force=True)
    mode = quota_mode(snapshot)
    tier = choose_model_tier(snapshot, role, priority)
    model = {
        "high": "gpt-5.6-sol",
        "balanced": "gpt-5.6-terra",
        "economy": "gpt-5.6-luna",
    }.get(tier, "gpt-5.6-terra")
    return {
        "mode": mode,
        "model_tier": tier,
        "recommended_model": model,
        "reason": decision_reason(snapshot, mode),
        "defer_until": snapshot.reset_at if mode == "blocked" else None,
        "quota": snapshot.to_dict(),
        "orchestrator_cli_hint": (
            "python3 run.py execute-topic --task <task> --topic <topic> "
            "--repo <repo> --sandbox <read-only|workspace-write> --model %s"
        )
        % model,
        "note": "This reads the local Codex App Server only; it never reads OAuth tokens or calls a model API.",
    }


def _object(properties: Dict[str, Any], required=()) -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(required),
    }


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "compile_engineering_goal",
        "description": (
            "Compile a human goal into the continuity-first LobeHub task contract: one owner "
            "context, deterministic checks, fresh-context falsification when useful, at most "
            "one repair round, and human escalation only at accountable decision points."
        ),
        "inputSchema": _object(
            {
                "goal": {"type": "string"},
                "user_outcome": {"type": "string"},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                "non_goals": {"type": "array", "items": {"type": "string"}},
                "prohibited_behaviors": {"type": "array", "items": {"type": "string"}},
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "array", "items": {"type": "string"}},
                "change_surfaces": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(CHANGE_SURFACES)},
                },
                "security_boundaries": {"type": "array", "items": {"type": "string"}},
                "rollback_plan": {"type": "string"},
                "observability_signals": {"type": "array", "items": {"type": "string"}},
                "performance_budgets": {"type": "array", "items": {"type": "string"}},
                "risk": {"type": "string", "enum": list(RISK_LEVELS)},
                "subjective": {"type": "boolean"},
                "irreversible": {"type": "boolean"},
                "external_effects": {"type": "boolean"},
                "security_sensitive": {"type": "boolean"},
            },
            required=("goal",),
        ),
        "handler": compile_work_contract,
    },
    {
        "name": "route_to_context",
        "description": (
            "Select the most compatible existing LobeHub topic by project, repository, paths "
            "and concepts. Adversarial review always receives a fresh topic."
        ),
        "inputSchema": _object(
            {
                "work": {"type": "string"},
                "purpose": {
                    "type": "string",
                    "enum": ["delivery", "repair", "research", "adversarial_review"],
                },
                "project_id": {"type": "string"},
                "repo_path": {"type": "string"},
                "touched_paths": {"type": "array", "items": {"type": "string"}},
                "candidates": {"type": "array", "items": {"type": "object"}},
            },
            required=("work", "candidates"),
        ),
        "handler": route_context,
    },
    {
        "name": "get_codex_quota_advice",
        "description": (
            "Read the locally authenticated Codex subscription quota and recommend a LobeHub "
            "Codex model tier without API keys or extra purchases."
        ),
        "inputSchema": _object(
            {
                "work_type": {"type": "string"},
                "priority": {"type": "integer", "minimum": 0, "maximum": 100},
            }
        ),
        "handler": quota_advice,
    },
    {
        "name": "compare_engineering_contracts",
        "description": (
            "Compare a frozen work contract with a candidate revision. Any change to outcome, "
            "scope, risk, safety or acceptance boundaries requires an explicit human decision."
        ),
        "inputSchema": _object(
            {
                "baseline": {"type": "object"},
                "candidate": {"type": "object"},
            },
            required=("baseline", "candidate"),
        ),
        "handler": compare_contracts,
    },
    {
        "name": "build_verification_plan",
        "description": (
            "Build a risk-adaptive verification DAG: deterministic preconditions first, then "
            "only the independent read-only falsification lanes justified by the change."
        ),
        "inputSchema": _object(
            {"contract": {"type": "object"}}, required=("contract",)
        ),
        "handler": build_verification_plan,
    },
    {
        "name": "aggregate_verification_findings",
        "description": (
            "Filter low-confidence review noise, deduplicate evidence-backed findings and decide "
            "pass, one consolidated repair, or human escalation."
        ),
        "inputSchema": _object(
            {
                "findings": {"type": "array", "items": {"type": "object"}},
                "program_gates": {"type": "array", "items": {"type": "object"}},
                "required_lanes": {"type": "array", "items": {"type": "string"}},
                "completed_lanes": {"type": "array", "items": {"type": "string"}},
                "repair_rounds_used": {"type": "integer", "minimum": 0},
                "human_gate_required": {"type": "boolean"},
            },
            required=("findings",),
        ),
        "handler": aggregate_findings,
    },
    {
        "name": "decide_release_readiness",
        "description": (
            "Decide whether evidence supports local delivery, a human decision, or release. "
            "This tool never performs merge, deploy, publish or any other external action."
        ),
        "inputSchema": _object(
            {
                "contract": {"type": "object"},
                "verification": {"type": "object"},
                "human_approved": {"type": "boolean"},
                "release_requested": {"type": "boolean"},
            },
            required=("contract", "verification"),
        ),
        "handler": decide_release_readiness,
    },
]
TOOLS_BY_NAME = {item["name"]: item for item in TOOLS}


class GovernanceMCP:
    def handle(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        request_id = request.get("id")
        notification = "id" not in request
        method = request.get("method")
        if method == "initialize":
            params = request.get("params") or {}
            asked = params.get("protocolVersion")
            version = asked if asked in SUPPORTED_PROTOCOLS else PROTOCOL_VERSION
            return self._result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "engineering-governance", "version": "0.4.0"},
                    "instructions": (
                        "LobeHub owns task state, topics, execution and acceptance. These tools "
                        "only freeze work contracts, route continuity, build evidence policy, "
                        "aggregate read-only findings and advise on local Codex quota."
                    ),
                },
            )
        if method in ("notifications/initialized", "initialized") or (
            isinstance(method, str) and method.startswith("notifications/")
        ):
            return None
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            tools = [{key: value for key, value in item.items() if key != "handler"} for item in TOOLS]
            return self._result(request_id, {"tools": tools})
        if method == "tools/call":
            return self._call(request_id, request.get("params") or {})
        if notification:
            return None
        return self._error(request_id, -32601, "Method not found: %s" % method)

    def _call(self, request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        tool = TOOLS_BY_NAME.get(str(params.get("name", "")))
        if not tool:
            return self._error(request_id, -32602, "Unknown tool: %s" % params.get("name"))
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return self._error(request_id, -32602, "arguments must be an object")
        try:
            payload = tool["handler"](arguments)
            return self._result(request_id, self._content(payload))
        except (ValueError, TypeError) as exc:
            return self._result(request_id, self._content(str(exc), error=True))

    def serve(self, stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
        source, target = stdin or sys.stdin, stdout or sys.stdout
        for line in source:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
                response = self.handle(request)
            except ValueError as exc:
                response = self._error(None, -32700, "Parse error: %s" % exc)
            if response is not None:
                target.write(json.dumps(response, ensure_ascii=False) + "\n")
                target.flush()
        return 0

    @staticmethod
    def _result(request_id: Any, result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _content(payload: Any, error: bool = False) -> Dict[str, Any]:
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
        result: Dict[str, Any] = {"content": [{"type": "text", "text": text}]}
        if error:
            result["isError"] = True
        return result


def main() -> int:
    return GovernanceMCP().serve()
