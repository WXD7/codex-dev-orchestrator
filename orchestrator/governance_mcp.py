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
from .governance_learning import (
    compile_bad_case_registry,
    compile_inspector_calibration,
)


PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "ai-delivery-governance"
SERVER_VERSION = "0.6.0"


class GovernanceToolError(ValueError):
    pass


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GovernanceToolError("%s must be an object" % name)
    return value


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "compile_technology_research",
        "description": (
            "Compile community, recent academic, open-source, and official evidence "
            "into comparable frameworks and technology paths. A fresh read-only "
            "quality review is mandatory; this tool does not browse or choose for a human."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "research_question": {"type": "string"},
                "human_scope": {"type": "array", "items": {"type": "string"}},
                "as_of": {"type": "string"},
                "queries": {"type": "object"},
                "sources": {"type": "array", "items": {"type": "object"}},
                "framework_candidates": {"type": "array", "items": {"type": "object"}},
                "technology_paths": {"type": "array", "items": {"type": "object"}},
                "recommendation": {"type": "object"},
                "review_declaration": {"type": "object"},
                "review_findings": {"type": "array", "items": {"type": "object"}},
                "review_verdict": {"type": "string", "enum": ["PASS", "BLOCKED"]},
            },
            "required": [
                "research_question",
                "human_scope",
                "as_of",
                "queries",
                "sources",
                "framework_candidates",
                "technology_paths",
                "recommendation",
                "review_declaration",
                "review_findings",
                "review_verdict",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "compile_intent_brief",
        "description": (
            "Compile the original request, final outcomes, executor/runtime split, "
            "technical choices, examples, and risk boundaries into a hash-bound brief. "
            "It asks questions but cannot confirm them for the human."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "original_request": {"type": "string"},
                "conversation_refs": {"type": "array", "items": {"type": "string"}},
                "expected_outcomes": {"type": "array", "items": {"type": "string"}},
                "acceptance_examples": {"type": "array", "items": {"type": "object"}},
                "development_executor": {"type": "object"},
                "product_runtime": {"type": "object"},
                "technical_choices": {"type": "array", "items": {"type": "object"}},
                "technology_research": {"type": "object"},
                "technology_strategy": {"type": "object"},
                "non_goals": {"type": "array", "items": {"type": "string"}},
                "risk_boundaries": {"type": "array", "items": {"type": "string"}},
                "research_refs": {"type": "array", "items": {"type": "string"}},
                "unresolved_questions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["original_request"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compile_intent_inspection",
        "description": (
            "Compile a fresh read-only inspection comparing the original request, "
            "research, intent brief, proposed contract, and acceptance examples. "
            "Blocking findings become human questions and cannot be self-fixed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "brief": {"type": "object"},
                "proposed_contract_source": {"type": "object"},
                "technology_research": {"type": "object"},
                "research_evidence": {"type": "array", "items": {"type": "string"}},
                "evidence_inputs": {"type": "array", "items": {"type": "string"}},
                "inspector_declaration": {"type": "object"},
                "coverage": {"type": "array", "items": {"type": "object"}},
                "findings": {"type": "array", "items": {"type": "object"}},
                "verdict": {"type": "string", "enum": ["PASS", "BLOCKED"]},
            },
            "required": [
                "brief",
                "proposed_contract_source",
                "evidence_inputs",
                "inspector_declaration",
                "coverage",
                "findings",
                "verdict",
            ],
            "additionalProperties": False,
        },
    },
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
                "environment": {
                    "type": "object",
                    "properties": {
                        "required_commands": {"type": "array", "items": {"type": "string"}},
                        "required_ports": {"type": "array", "items": {"type": "integer"}},
                    },
                    "additionalProperties": False,
                },
                "uncertainties": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "decision_id": {"type": "string"},
                            "category": {
                                "type": "string",
                                "enum": [
                                    "policy_choice",
                                    "domain_fact",
                                    "engineering_invariant",
                                    "researchable_fact",
                                ],
                            },
                            "statement": {"type": "string"},
                            "question": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": [
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
                                ],
                            },
                            "state": {
                                "type": "string",
                                "enum": [
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
                                ],
                            },
                            "impact": {
                                "type": "string",
                                "enum": ["low", "medium", "high", "critical"],
                            },
                            "acceptance_id": {"type": "string"},
                            "acceptance_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "consequence": {"type": "string"},
                            "proposed_default": {"type": "string"},
                            "decision_owner": {
                                "type": "string",
                                "enum": [
                                    "human",
                                    "domain_expert",
                                    "agent",
                                    "deterministic_rule",
                                ],
                            },
                            "reversible": {"type": "boolean"},
                            "answer": {"type": "string"},
                            "evidence": {"type": "string"},
                        },
                        "required": ["category", "statement"],
                        "additionalProperties": False,
                    },
                },
                "required_evidence_classes": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["property_test", "mutation_test", "browser_e2e"],
                    },
                },
                "must_kill_cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "counterexample": {"type": "string"},
                            "expected": {"type": "string"},
                        },
                        "required": ["title", "counterexample", "expected"],
                        "additionalProperties": False,
                    },
                },
                "intent_alignment": {
                    "type": "object",
                    "properties": {
                        "brief": {"type": "object"},
                        "inspection": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["goal"],
            "additionalProperties": False,
        },
    },
    {
        "name": "propose_contract_resolution",
        "description": (
            "Compile human-authored answers into a hash-bound contract delta. "
            "The proposal cannot attest approval, resume a task, or start an owner."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {"type": "object"},
                "answers": {"type": "array", "items": {"type": "object"}},
                "field_updates": {"type": "object"},
            },
            "required": ["contract", "answers"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compile_bad_case_registry",
        "description": (
            "Compile human-confirmed Good/Bad Cases into a hidden hash-bound registry. "
            "This tool cannot promote a candidate without supplied human confirmation evidence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "registry_id": {"type": "string"},
                "cases": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["registry_id", "cases"],
            "additionalProperties": False,
        },
    },
    {
        "name": "calibrate_inspector",
        "description": (
            "Measure one Inspector against human-labelled cases. It remains shadow-only "
            "unless every frozen recall, false-positive, agreement, and contribution threshold passes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lane_id": {"type": "string"},
                "evaluations": {"type": "array", "items": {"type": "object"}},
                "policy": {"type": "object"},
            },
            "required": ["lane_id", "evaluations"],
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
            "properties": {
                "contract": {"type": "object"},
                "bad_case_registry": {"type": "object"},
            },
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
                "inspector_telemetry": {"type": "array", "items": {"type": "object"}},
                "inspector_calibrations": {
                    "type": "array",
                    "items": {"type": "object"},
                },
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
    if name == "compile_technology_research":
        return engine.compile_technology_research(arguments)
    if name == "compile_intent_brief":
        return engine.compile_intent_brief(arguments)
    if name == "compile_intent_inspection":
        return engine.compile_intent_inspection(arguments)
    if name == "compile_work_contract":
        return engine.compile_contract(arguments)
    if name == "propose_contract_resolution":
        return engine.propose_contract_resolution(
            _object(arguments.get("contract"), "contract"),
            arguments.get("answers") or [],
            arguments.get("field_updates") or {},
        )
    if name == "compile_bad_case_registry":
        return compile_bad_case_registry(arguments)
    if name == "calibrate_inspector":
        return compile_inspector_calibration(arguments)
    if name == "plan_delivery":
        return engine.route(
            _object(arguments.get("contract"), "contract"),
            arguments.get("bad_case_registry"),
        )
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
