"""Stateless MCP surface for delivery governance.

Unlike the legacy board bridge, this server does not connect to HTTP or own a
database.  Every tool is a pure calculation over the provided JSON value, which
makes it safe to expose to LobeHub, Kandev, Codex, or another MCP client.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Mapping, Optional, TextIO

from .governance import GovernanceEngine, integration_blueprint


PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "ai-delivery-governance"
SERVER_VERSION = "0.2.0"


class GovernanceToolError(ValueError):
    pass


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GovernanceToolError("%s must be an object" % name)
    return value


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "compile_work_contract",
        "description": (
            "Compile a goal into a versioned work contract, detect missing decisions, "
            "and classify risk. This does not start work or approve anything."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "goal": {"type": "string"},
                "users": {"type": "array", "items": {"type": "string"}},
                "outcomes": {"type": "array", "items": {"type": "string"}},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                "non_goals": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "array", "items": {"type": "string"}},
                "forbidden_behaviors": {"type": "array", "items": {"type": "string"}},
                "human_decisions": {"type": "array", "items": {"type": "string"}},
                "deterministic_checks": {"type": "array", "items": {"type": "string"}},
                "change_types": {"type": "array", "items": {"type": "string"}},
                "risk_flags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["goal"],
            "additionalProperties": False,
        },
    },
    {
        "name": "plan_delivery",
        "description": (
            "Select deterministic and independent verification lanes from a compiled "
            "contract. Low-risk work stays single-owner; high-risk work fans out."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"contract": {"type": "object"}},
            "required": ["contract"],
            "additionalProperties": False,
        },
    },
    {
        "name": "build_inspector_contexts",
        "description": (
            "Build one minimal, read-only, fresh-context packet per semantic inspector. "
            "Peer findings and developer transcripts are excluded."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {"type": "object"},
                "plan": {"type": "object"},
            },
            "required": ["contract", "plan"],
            "additionalProperties": False,
        },
    },
    {
        "name": "build_delivery_handoff",
        "description": (
            "Build a side-effect-free Kandev/Codex execution manifest for the owner, "
            "risk-selected inspectors, sequencing, profiles, and human gates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {"type": "object"},
                "plan": {"type": "object"},
            },
            "required": ["contract", "plan"],
            "additionalProperties": False,
        },
    },
    {
        "name": "adjudicate_delivery",
        "description": (
            "Filter, deduplicate, and adjudicate deterministic results and independent "
            "findings. It can recommend one repair round but cannot modify code or approve a merge."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {"type": "object"},
                "plan": {"type": "object"},
                "deterministic_results": {"type": "array", "items": {"type": "object"}},
                "findings": {"type": "array", "items": {"type": "object"}},
                "repair_round": {"type": "integer", "minimum": 0},
            },
            "required": ["contract", "plan", "deterministic_results", "findings"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_integration_blueprint",
        "description": (
            "Return the ownership boundaries for LobeHub, Kandev, Symphony, Spec Kit, "
            "GitHub Actions, Codex, and this stateless governance layer."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def call_tool(name: str, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    engine = GovernanceEngine()
    if name == "compile_work_contract":
        return engine.compile_contract(arguments)
    if name == "plan_delivery":
        return engine.route(_object(arguments.get("contract"), "contract"))
    if name == "build_inspector_contexts":
        contract = _object(arguments.get("contract"), "contract")
        plan = _object(arguments.get("plan"), "plan")
        return {"contexts": engine.context_packets(contract, plan)}
    if name == "build_delivery_handoff":
        contract = _object(arguments.get("contract"), "contract")
        plan = _object(arguments.get("plan"), "plan")
        return engine.delivery_handoff(contract, plan)
    if name == "adjudicate_delivery":
        return engine.adjudicate(arguments)
    if name == "get_integration_blueprint":
        return integration_blueprint()
    raise GovernanceToolError("Unknown governance tool: %s" % name)


def _result(request_id: Any, value: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def handle(request: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None and method and method.startswith("notifications/"):
        return None
    if method == "initialize":
        params = request.get("params") or {}
        requested = params.get("protocolVersion") if isinstance(params, Mapping) else None
        protocol = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return _result(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "This server is stateless and advisory. It never runs agents, writes code, "
                    "persists tasks, approves work, or performs external actions."
                ),
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params") or {}
        if not isinstance(params, Mapping):
            return _error(request_id, -32602, "params must be an object")
        name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, Mapping):
            return _error(request_id, -32602, "arguments must be an object")
        try:
            value = call_tool(name, arguments)
        except (GovernanceToolError, ValueError) as exc:
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        return _result(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(value, ensure_ascii=False, indent=2),
                    }
                ],
                "structuredContent": value,
                "isError": False,
            },
        )
    return _error(request_id, -32601, "Method not found: %s" % method)


def serve(reader: TextIO = sys.stdin, writer: TextIO = sys.stdout) -> int:
    for raw in reader:
        raw = raw.strip()
        if not raw:
            continue
        try:
            value = json.loads(raw)
            if not isinstance(value, Mapping):
                response = _error(None, -32600, "Request must be an object")
            else:
                response = handle(value)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, "Parse error: %s" % exc)
        if response is not None:
            writer.write(json.dumps(response, ensure_ascii=False) + "\n")
            writer.flush()
    return 0


def main(argv=None) -> int:
    del argv
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
