"""Safe project scaffolding for the AI delivery governance layer."""

from __future__ import annotations

import json
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
