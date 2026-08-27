"""Safe project scaffolding for the AI delivery governance layer."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping

from .governance import GovernanceEngine, integration_blueprint
from .governance_learning import compile_bad_case_registry


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _write_new(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError("Refusing to overwrite existing file: %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _git_value(root: Path, *arguments: str) -> str:
    environment = dict(os.environ)
    for name in list(environment):
        upper = name.upper()
        if any(
            marker in upper
            for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
        ):
            environment.pop(name, None)
    completed = subprocess.run(
        ["git"] + list(arguments),
        cwd=str(root),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        raise ValueError("could not read Git identity for continuity bundle")
    return completed.stdout.strip()


def _optional_git_value(root: Path, *arguments: str) -> str:
    try:
        return _git_value(root, *arguments)
    except ValueError:
        return ""


def scaffold_project(target: Path, contract_source: Mapping[str, Any]) -> Dict[str, Any]:
    """Create a minimal, versionable policy bundle in an existing Git repository.

    No task database, agent credentials, workflow state, or vendor source is
    copied. Existing files are never overwritten.
    """
    root = Path(target).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("target must be an existing directory")
    if not (root / ".git").exists():
        raise ValueError("target must be a Git repository or worktree")

    engine = GovernanceEngine()
    contract = engine.compile_contract(contract_source)
    bad_case_registry = compile_bad_case_registry(
        {"registry_id": "project-bad-cases", "cases": []}
    )
    calibration_policy = {
        "schema_version": "2.3",
        "policy_id": "default-inspector-calibration",
        "default_for_new_or_changed_inspectors": "shadow",
        "thresholds": {
            "minimum_cases": 8,
            "minimum_positive_cases": 5,
            "minimum_negative_cases": 3,
            "minimum_recall": 0.9,
            "maximum_false_positive_rate": 0.1,
            "minimum_human_agreement": 0.9,
            "minimum_independent_contributions": 1,
        },
        "evaluation_requirements": [
            "case_hash",
            "labelled_by",
            "label_evidence",
            "expected_defect",
            "reported_defect",
            "human_agrees",
            "independent_contribution",
        ],
        "promotion": {
            "automatic": False,
            "effect": "blocking eligibility only; never merge or release authority",
        },
    }
    plan = engine.route(contract, bad_case_registry)
    handoff = engine.delivery_handoff(contract, plan)
    governance_dir = root / ".ai-delivery"
    files = {
        governance_dir / "technology-research.json": json.dumps(
            ((contract.get("intent_alignment") or {}).get("brief") or {}).get(
                "technology_research"
            )
            or {},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        governance_dir / "intent-brief.json": json.dumps(
            (contract.get("intent_alignment") or {}).get("brief") or {},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        governance_dir / "intent-inspection.json": json.dumps(
            (contract.get("intent_alignment") or {}).get("inspection") or {},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        governance_dir / "contract.json": json.dumps(
            contract, ensure_ascii=False, indent=2
        )
        + "\n",
        governance_dir / "verification-plan.json": json.dumps(
            plan, ensure_ascii=False, indent=2
        )
        + "\n",
        governance_dir / "integrations.json": json.dumps(
            integration_blueprint(), ensure_ascii=False, indent=2
        )
        + "\n",
        governance_dir / "delivery-handoff.json": json.dumps(
            handoff, ensure_ascii=False, indent=2
        )
        + "\n",
        governance_dir / "bad-case-registry.json": json.dumps(
            bad_case_registry, ensure_ascii=False, indent=2
        )
        + "\n",
        governance_dir / "calibration-policy.json": json.dumps(
            calibration_policy, ensure_ascii=False, indent=2
        )
        + "\n",
        governance_dir / "runtime-protocol.json": json.dumps(
            {
                "schema_version": "2.3",
                "contract_hash": contract["contract_hash"],
                "plan_hash": plan["plan_hash"],
                "bad_case_registry_hash": bad_case_registry["registry_hash"],
                "sequence": plan["sequence"],
                "artifact_invariants": plan["artifact_invariants"],
                "checkpoint_policy": plan["checkpoint_policy"],
                "telemetry_schema": plan["telemetry_schema"],
                "final_verifier": plan["final_verifier"],
                "must_kill_cases": plan["must_kill_cases"],
                "question_gate": contract["question_gate"],
                "intent_alignment": {
                    "required": (contract.get("intent_alignment") or {}).get(
                        "required", False
                    ),
                    "intent_hash": (contract.get("intent_alignment") or {}).get(
                        "intent_hash", ""
                    ),
                    "inspection_hash": (
                        contract.get("intent_alignment") or {}
                    ).get("inspection_hash", ""),
                    "research_hash": (
                        contract.get("intent_alignment") or {}
                    ).get("research_hash", ""),
                    "technology_strategy_hash": (
                        contract.get("intent_alignment") or {}
                    ).get("technology_strategy_hash", ""),
                    "human_attestation_required": (
                        contract.get("intent_alignment") or {}
                    ).get("human_attestation_required", False),
                    "owner_creation_before_attestation": False,
                    "activation": "trusted controller calls activate_delivery_handoff with a new replayable signed ledger",
                },
                "technology_race": plan.get("technology_race") or {},
                "contract_resolution": {
                    "proposal_is_not_approval": True,
                    "human_delta_attestation_required": True,
                    "binds": [
                        "parent_contract_hash",
                        "delta_hash",
                        "proposed_contract_hash",
                        "proposed_plan_hash",
                        "external_task_ref",
                        "parent_run_when_present",
                    ],
                    "owner_creation_requires_ready_contract": True,
                },
                "operator_snapshot": handoff.get("operator_view") or {},
                "review_packet": {
                    "source": "signed controller ledger",
                    "separates_execution_from_delivery_verdict": True,
                    "external_actions_allowed": False,
                    "automated_approval": False,
                },
                "learning": {
                    "bad_case_registry": "bad-case-registry.json",
                    "calibration_policy": "calibration-policy.json",
                    "confirmed_cases_require_human_evidence": True,
                    "new_or_changed_inspectors_start_in_shadow": True,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        governance_dir / "CONSTITUTION.md": (TEMPLATE_DIR / "CONSTITUTION.md").read_text(
            encoding="utf-8"
        ),
        governance_dir / "SYMPHONY_POLICY.md": (
            TEMPLATE_DIR / "SYMPHONY_POLICY.md"
        ).read_text(encoding="utf-8"),
        governance_dir / "KANDEV_RUNBOOK.md": (
            TEMPLATE_DIR / "KANDEV_RUNBOOK.md"
        ).read_text(encoding="utf-8"),
    }
    git_branch = _git_value(root, "branch", "--show-current")
    git_head = _git_value(root, "rev-parse", "HEAD")
    git_remote = _optional_git_value(root, "remote", "get-url", "origin")
    project_id = "prj_%s" % hashlib.sha256(
        (root.name + "\0" + git_head).encode("utf-8")
    ).hexdigest()[:16]
    driver_bootstrap_path = governance_dir / "DRIVER_BOOTSTRAP.md"
    files[driver_bootstrap_path] = (TEMPLATE_DIR / "DRIVER_BOOTSTRAP.md").read_text(
        encoding="utf-8"
    )
    canonical_relative_paths = [
        ".ai-delivery/DRIVER_BOOTSTRAP.md",
        ".ai-delivery/contract.json",
        ".ai-delivery/verification-plan.json",
        ".ai-delivery/delivery-handoff.json",
        ".ai-delivery/runtime-protocol.json",
        ".ai-delivery/KANDEV_RUNBOOK.md",
    ]
    evidence_index = {
        "schema_version": "1.0",
        "artifact": "ai_delivery_evidence_index",
        "project_id": project_id,
        "entries": [
            {
                "id": "bootstrap-%02d" % (index + 1),
                "path": relative_path,
                "sha256": _sha256_text(files[root / relative_path]),
                "kind": "policy_or_contract",
                "authority": "current",
            }
            for index, relative_path in enumerate(canonical_relative_paths)
        ],
    }
    evidence_index_path = governance_dir / "EVIDENCE_INDEX.json"
    files[evidence_index_path] = json.dumps(
        evidence_index, ensure_ascii=False, indent=2
    ) + "\n"
    current_state = {
        "schema_version": "1.0",
        "artifact": "ai_delivery_current_state",
        "project": {
            "project_id": project_id,
            "name": root.name,
            "git": {
                "branch": git_branch,
                "baseline_commit": git_head,
                **({"remote_url": git_remote} if git_remote else {}),
            },
        },
        "workflow": {
            "policy_version": "2.4",
            "governance_schema_version": "2.3",
            "continuity_protocol_version": "1.0",
        },
        "isolation": {
            "bind_before_read": True,
            "project_sources_relative_only": True,
            "cross_project_product_context": "deny",
            "cross_project_project_cases": "deny",
            "global_learning": "generic_governance_only",
        },
        "current": {
            "phase": "governance_bundle_initialized",
            "execution_state": "not_started",
            "delivery_verdict": "unreviewed",
            "contract_status": contract["status"],
            "handoff_status": handoff["status"],
        },
        "evidence_index": ".ai-delivery/EVIDENCE_INDEX.json",
        "required_read_order": [
            ".ai-delivery/CURRENT_STATE.json",
            ".ai-delivery/DRIVER_BOOTSTRAP.md",
            ".ai-delivery/contract.json",
            ".ai-delivery/verification-plan.json",
            ".ai-delivery/delivery-handoff.json",
            ".ai-delivery/runtime-protocol.json",
            ".ai-delivery/KANDEV_RUNBOOK.md",
        ],
        "canonical_sources": [
            {
                "path": ".ai-delivery/CURRENT_STATE.json",
                "purpose": "single current project and resume state",
            }
        ]
        + [
            {"path": relative_path, "purpose": "hash-indexed takeover source"}
            for relative_path in canonical_relative_paths
        ],
        "historical_sources": [],
        "consistency_assertions": [
            {
                "id": "contract-status",
                "kind": "json_equals",
                "path": ".ai-delivery/contract.json",
                "pointer": "/status",
                "value": contract["status"],
            },
            {
                "id": "handoff-status",
                "kind": "json_equals",
                "path": ".ai-delivery/delivery-handoff.json",
                "pointer": "/status",
                "value": handoff["status"],
            },
        ],
        "resume": {
            "allowed_next_actions": [
                "read the frozen project-local contract and handoff",
                "resolve only the declared human or evidence gates",
            ],
            "prohibited_actions": [
                "read sibling project state",
                "import another project's decisions or cases",
                "push",
                "merge",
                "deploy",
                "publish",
                "spend money",
                "use secrets without explicit product authorization",
            ],
            "needs_human": bool(handoff.get("status") != "ready_for_control_plane"),
        },
        "cold_start_acceptance": [
            {"id": "project-id", "expected": project_id},
            {"id": "contract-status", "expected": contract["status"]},
            {"id": "handoff-status", "expected": handoff["status"]},
            {"id": "cross-project-import", "expected": "deny"},
        ],
    }
    files[governance_dir / "CURRENT_STATE.json"] = json.dumps(
        current_state, ensure_ascii=False, indent=2
    ) + "\n"
    for path in files:
        if path.exists():
            raise FileExistsError(
                "Governance bundle already exists; no files were changed: %s" % path
            )
    for path, content in files.items():
        _write_new(path, content)
    return {
        "target": str(root),
        "contract_id": contract["contract_id"],
        "contract_status": contract["status"],
        "files": [str(path.relative_to(root)) for path in files],
        "next": (
            "Resolve the questions in .ai-delivery/contract.json and regenerate the "
            "bundle before implementation."
            if contract["status"] != "ready"
            else "Follow .ai-delivery/delivery-handoff.json with the profile boundaries "
            "in KANDEV_RUNBOOK.md, or incorporate SYMPHONY_POLICY.md into the real "
            "repository WORKFLOW.md."
        ),
    }
