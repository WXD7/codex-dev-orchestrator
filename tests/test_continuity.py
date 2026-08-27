from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator.continuity import build_takeover_packet
from tests.helpers import make_git_repo


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git"] + list(arguments),
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def write_continuity_packet(repo: Path, project_id: str, marker: str) -> dict:
    governance = repo / ".ai-delivery"
    governance.mkdir()
    (repo / "AGENTS.md").write_text(
        "# Project instructions\nCurrent marker: %s\n" % marker,
        encoding="utf-8",
    )
    (governance / "DRIVER_BOOTSTRAP.md").write_text(
        "# Fresh-driver bootstrap\nProject isolation is mandatory.\n",
        encoding="utf-8",
    )
    (governance / "contract.json").write_text(
        json.dumps({"status": "ready", "marker": marker}) + "\n",
        encoding="utf-8",
    )
    entries = []
    for index, relative in enumerate(
        ["AGENTS.md", ".ai-delivery/DRIVER_BOOTSTRAP.md", ".ai-delivery/contract.json"]
    ):
        entries.append(
            {
                "id": "evidence-%d" % index,
                "path": relative,
                "sha256": sha256(repo / relative),
                "kind": "policy_or_contract",
                "authority": "current",
            }
        )
    evidence = {
        "schema_version": "1.0",
        "artifact": "ai_delivery_evidence_index",
        "project_id": project_id,
        "entries": entries,
    }
    (governance / "EVIDENCE_INDEX.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )
    baseline = git(repo, "rev-parse", "HEAD")
    state = {
        "schema_version": "1.0",
        "artifact": "ai_delivery_current_state",
        "project": {
            "project_id": project_id,
            "name": repo.name,
            "git": {"branch": "main", "baseline_commit": baseline},
        },
        "workflow": {"policy_version": "2.4"},
        "isolation": {
            "bind_before_read": True,
            "project_sources_relative_only": True,
            "cross_project_product_context": "deny",
            "cross_project_project_cases": "deny",
            "global_learning": "generic_governance_only",
        },
        "current": {"phase": "ready", "marker": marker},
        "evidence_index": ".ai-delivery/EVIDENCE_INDEX.json",
        "required_read_order": [
            ".ai-delivery/CURRENT_STATE.json",
            "AGENTS.md",
            ".ai-delivery/DRIVER_BOOTSTRAP.md",
            ".ai-delivery/contract.json",
        ],
        "canonical_sources": [
            {"path": ".ai-delivery/CURRENT_STATE.json", "purpose": "current state"},
            {"path": "AGENTS.md", "purpose": "project policy"},
            {"path": ".ai-delivery/DRIVER_BOOTSTRAP.md", "purpose": "entrypoint"},
            {"path": ".ai-delivery/contract.json", "purpose": "contract"},
        ],
        "historical_sources": [],
        "consistency_assertions": [
            {
                "id": "project-marker",
                "kind": "text_contains",
                "path": "AGENTS.md",
                "values": [marker],
            },
            {
                "id": "contract-status",
                "kind": "json_equals",
                "path": ".ai-delivery/contract.json",
                "pointer": "/status",
                "value": "ready",
            },
        ],
        "resume": {
            "allowed_next_actions": ["inspect"],
            "prohibited_actions": ["cross-project import"],
            "needs_human": False,
        },
        "cold_start_acceptance": [
            {"id": "project-id", "expected": project_id},
            {"id": "marker", "expected": marker},
        ],
    }
    (governance / "CURRENT_STATE.json").write_text(
        json.dumps(state, indent=2) + "\n", encoding="utf-8"
    )
    return state


class ContinuityTests(unittest.TestCase):
    def test_workspace_router_template_contains_no_product_memory(self):
        template = (
            Path(__file__).resolve().parent.parent
            / "orchestrator/templates/WORKSPACE_ROUTER_AGENTS.md"
        ).read_text(encoding="utf-8")
        self.assertIn("exactly one Git repository", template)
        self.assertIn("Never search a sibling repository", template)
        for product_marker in (
            "german-legal-billing",
            "Gegenstandswert",
            "DeepSeek",
            "1963.50",
        ):
            self.assertNotIn(product_marker, template)

    def test_new_codex_guide_is_prominent_generic_and_copyable(self):
        root = Path(__file__).resolve().parent.parent
        guide = (root / "START_HERE_NEW_CODEX.md").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertIn("START_HERE_NEW_CODEX.md", readme)
        self.assertIn("请启用 AI Delivery Governance 的无历史驾驶员接管", guide)
        self.assertIn("ready_for_takeover: true", guide)
        self.assertIn("governance init", guide)
        for product_marker in (
            "german-legal-billing",
            "Gegenstandswert",
            "DeepSeek",
            "1963.50",
        ):
            self.assertNotIn(product_marker, guide)

    def test_fresh_driver_packet_is_ready_without_conversation_history(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            write_continuity_packet(repo, "project-alpha", "ALPHA_ONLY")
            packet = build_takeover_packet(repo)

        self.assertTrue(packet["ready_for_takeover"])
        self.assertEqual(packet["project_id"], "project-alpha")
        self.assertEqual(packet["current"]["marker"], "ALPHA_ONLY")
        self.assertEqual(packet["isolation"]["cross_project_product_context"], "deny")
        self.assertNotIn("codex://threads/", json.dumps(packet))

    def test_two_projects_do_not_leak_product_context(self):
        with tempfile.TemporaryDirectory() as directory:
            first_root = Path(directory) / "first"
            second_root = Path(directory) / "second"
            first_root.mkdir()
            second_root.mkdir()
            first = make_git_repo(first_root)
            second = make_git_repo(second_root)
            write_continuity_packet(first, "project-alpha", "ALPHA_LEGAL_FACT")
            write_continuity_packet(second, "project-beta", "BETA_RETAIL_FACT")

            first_packet = json.dumps(build_takeover_packet(first), ensure_ascii=False)
            second_packet = json.dumps(build_takeover_packet(second), ensure_ascii=False)

        self.assertIn("ALPHA_LEGAL_FACT", first_packet)
        self.assertNotIn("BETA_RETAIL_FACT", first_packet)
        self.assertIn("BETA_RETAIL_FACT", second_packet)
        self.assertNotIn("ALPHA_LEGAL_FACT", second_packet)

    def test_hash_mismatch_blocks_takeover(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            write_continuity_packet(repo, "project-alpha", "ALPHA_ONLY")
            (repo / ".ai-delivery/contract.json").write_text(
                json.dumps({"status": "changed"}) + "\n", encoding="utf-8"
            )
            packet = build_takeover_packet(repo)

        self.assertFalse(packet["ready_for_takeover"])
        self.assertTrue(any("hash mismatch" in item for item in packet["errors"]))

    def test_conversation_reference_blocks_takeover(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            state = write_continuity_packet(repo, "project-alpha", "ALPHA_ONLY")
            state["conversation_refs"] = ["codex://threads/not-portable"]
            (repo / ".ai-delivery/CURRENT_STATE.json").write_text(
                json.dumps(state, indent=2) + "\n", encoding="utf-8"
            )
            packet = build_takeover_packet(repo)

        self.assertFalse(packet["ready_for_takeover"])
        self.assertTrue(
            any("conversation-dependent" in item for item in packet["errors"])
        )

    def test_project_source_cannot_escape_selected_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            state = write_continuity_packet(repo, "project-alpha", "ALPHA_ONLY")
            state["required_read_order"].append("../other-project/CURRENT_STATE.json")
            (repo / ".ai-delivery/CURRENT_STATE.json").write_text(
                json.dumps(state, indent=2) + "\n", encoding="utf-8"
            )
            packet = build_takeover_packet(repo)

        self.assertFalse(packet["ready_for_takeover"])
        self.assertTrue(any("escapes selected repository" in item for item in packet["errors"]))

    def test_unindexed_required_source_blocks_takeover(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            state = write_continuity_packet(repo, "project-alpha", "ALPHA_ONLY")
            (repo / "unbound-notes.md").write_text("not hash bound\n", encoding="utf-8")
            state["required_read_order"].append("unbound-notes.md")
            (repo / ".ai-delivery/CURRENT_STATE.json").write_text(
                json.dumps(state, indent=2) + "\n", encoding="utf-8"
            )
            packet = build_takeover_packet(repo)

        self.assertFalse(packet["ready_for_takeover"])
        self.assertTrue(any("not hash-indexed" in item for item in packet["errors"]))

    def test_duplicate_evidence_path_blocks_takeover(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            write_continuity_packet(repo, "project-alpha", "ALPHA_ONLY")
            index_path = repo / ".ai-delivery/EVIDENCE_INDEX.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            duplicate = dict(index["entries"][0])
            duplicate["id"] = "duplicate"
            index["entries"].append(duplicate)
            index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
            packet = build_takeover_packet(repo)

        self.assertFalse(packet["ready_for_takeover"])
        self.assertTrue(any("duplicate evidence path" in item for item in packet["errors"]))

    def test_dirty_worktree_keeps_first_hidden_path_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            write_continuity_packet(repo, "project-alpha", "ALPHA_ONLY")
            hidden = repo / ".hidden-state"
            hidden.write_text("before\n", encoding="utf-8")
            git(repo, "add", ".")
            git(repo, "commit", "-m", "add continuity state")
            hidden.write_text("after\n", encoding="utf-8")
            packet = build_takeover_packet(repo)

        self.assertTrue(packet["ready_for_takeover"])
        self.assertEqual(packet["git"]["changed_paths"][0], ".hidden-state")

    def test_missing_current_state_requires_explicit_project_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = make_git_repo(Path(directory))
            packet = build_takeover_packet(repo)

        self.assertFalse(packet["ready_for_takeover"])
        self.assertIn("explicit project initialization", packet["errors"][0])


if __name__ == "__main__":
    unittest.main()
