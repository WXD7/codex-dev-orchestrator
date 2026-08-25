"""Safe project scaffolding for the AI delivery governance layer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from .governance import GovernanceEngine, integration_blueprint


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
    plan = engine.route(contract)
    handoff = engine.delivery_handoff(contract, plan)
    governance_dir = root / ".ai-delivery"
    files = {
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
