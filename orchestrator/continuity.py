"""Project-isolated, conversation-independent driver takeover verification.

The governance engine is deliberately stateless.  This module therefore does
not try to remember a conversation.  It verifies a versioned continuity packet
inside one selected Git repository and emits the minimum context a fresh driver
needs to take over without importing another project's product state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


CONTINUITY_VERSION = "1.0"
STATE_PATH = Path(".ai-delivery/CURRENT_STATE.json")
DEFAULT_EVIDENCE_INDEX_PATH = Path(".ai-delivery/EVIDENCE_INDEX.json")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID_RE = re.compile(r"^[0-9a-f]{40,64}$")
_FORBIDDEN_STATE_KEYS = {"conversation_refs", "thread_id", "source_thread_id"}
_FORBIDDEN_STATE_TEXT = ("codex://threads/", "DEEPSEEK_API_KEY=")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object: %s" % path)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_project_path(root: Path, raw: Any) -> Path:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("project-relative path is required")
    relative = Path(text)
    if relative.is_absolute():
        raise ValueError("project source must be relative, not absolute: %s" % text)
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("project source escapes selected repository: %s" % text) from exc
    return resolved


def _sanitized_git_environment() -> Dict[str, str]:
    environment = dict(os.environ)
    for name in list(environment):
        upper = name.upper()
        if any(
            marker in upper
            for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
        ):
            environment.pop(name, None)
    return environment


def _git(root: Path, arguments: Sequence[str]) -> Tuple[int, str]:
    completed = subprocess.run(
        ["git"] + list(arguments),
        cwd=str(root),
        env=_sanitized_git_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=10,
    )
    # Preserve the leading status column from `git status --porcelain`; using
    # strip() here corrupts the first modified path (for example `.ai-delivery`
    # becomes `ai-delivery`). Other Git identity values have no meaningful
    # leading whitespace, so removing trailing line endings is sufficient.
    output = (completed.stdout or completed.stderr).rstrip()
    return completed.returncode, output


def _json_pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise ValueError("JSON pointer must start with '/': %s" % pointer)
    current = value
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise ValueError("JSON pointer does not resolve: %s" % pointer) from exc
        elif isinstance(current, Mapping) and token in current:
            current = current[token]
        else:
            raise ValueError("JSON pointer does not resolve: %s" % pointer)
    return current


def _scan_for_conversation_dependency(value: Any, location: str = "state") -> List[str]:
    problems: List[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_location = "%s.%s" % (location, key_text)
            if key_text in _FORBIDDEN_STATE_KEYS:
                problems.append("conversation-dependent key is forbidden: %s" % child_location)
            problems.extend(_scan_for_conversation_dependency(child, child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            problems.extend(
                _scan_for_conversation_dependency(child, "%s[%d]" % (location, index))
            )
    elif isinstance(value, str):
        for forbidden in _FORBIDDEN_STATE_TEXT:
            if forbidden in value:
                problems.append(
                    "conversation or secret value dependency is forbidden at %s" % location
                )
    return problems


def _base_packet(root: Path) -> Dict[str, Any]:
    return {
        "schema_version": CONTINUITY_VERSION,
        "artifact": "ai_delivery_driver_takeover",
        "generated_at": _utc_now(),
        "selected_repository": str(root),
        "status": "blocked",
        "ready_for_takeover": False,
        "project_id": "",
        "checks": [],
        "warnings": [],
        "errors": [],
    }


def _record_check(
    packet: Dict[str, Any], check_id: str, passed: bool, summary: str, blocking: bool = True
) -> None:
    packet["checks"].append(
        {
            "id": check_id,
            "passed": bool(passed),
            "blocking": bool(blocking),
            "summary": summary,
        }
    )
    if passed:
        return
    collection = "errors" if blocking else "warnings"
    packet[collection].append(summary)


def _validate_isolation(packet: Dict[str, Any], state: Mapping[str, Any]) -> None:
    isolation = state.get("isolation") or {}
    if not isinstance(isolation, Mapping):
        _record_check(packet, "project-isolation", False, "isolation must be an object")
        return
    expected = {
        "bind_before_read": True,
        "project_sources_relative_only": True,
        "cross_project_product_context": "deny",
        "cross_project_project_cases": "deny",
        "global_learning": "generic_governance_only",
    }
    mismatches = [
        "%s=%r" % (key, isolation.get(key))
        for key, expected_value in expected.items()
        if isolation.get(key) != expected_value
    ]
    _record_check(
        packet,
        "project-isolation",
        not mismatches,
        (
            "project isolation contract is complete"
            if not mismatches
            else "project isolation contract mismatch: %s" % ", ".join(mismatches)
        ),
    )


def _validate_evidence_index(
    packet: Dict[str, Any], root: Path, state: Mapping[str, Any]
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Mapping[str, Any]]]:
    raw_index_path = state.get("evidence_index") or str(DEFAULT_EVIDENCE_INDEX_PATH)
    try:
        index_path = _safe_project_path(root, raw_index_path)
    except ValueError as exc:
        _record_check(packet, "evidence-index", False, str(exc))
        return None, {}
    if not index_path.is_file():
        _record_check(packet, "evidence-index", False, "missing evidence index: %s" % raw_index_path)
        return None, {}
    try:
        index = _read_object(index_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _record_check(packet, "evidence-index", False, "invalid evidence index: %s" % exc)
        return None, {}
    project_id = str(((state.get("project") or {}).get("project_id") or ""))
    header_ok = (
        index.get("schema_version") == CONTINUITY_VERSION
        and index.get("artifact") == "ai_delivery_evidence_index"
        and index.get("project_id") == project_id
    )
    _record_check(
        packet,
        "evidence-index-header",
        header_ok,
        "evidence index is bound to the selected project"
        if header_ok
        else "evidence index header or project_id does not match CURRENT_STATE",
    )
    entries = index.get("entries") or []
    if not isinstance(entries, list) or not entries:
        _record_check(packet, "evidence-index-entries", False, "evidence index has no entries")
        return index, {}
    by_path: Dict[str, Mapping[str, Any]] = {}
    for position, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            _record_check(
                packet,
                "evidence-entry-%d" % position,
                False,
                "evidence entry %d must be an object" % position,
            )
            continue
        raw_path = str(entry.get("path") or "")
        expected_hash = str(entry.get("sha256") or "")
        if raw_path in by_path:
            _record_check(
                packet,
                "evidence-entry-%d" % position,
                False,
                "duplicate evidence path is forbidden: %s" % raw_path,
            )
            continue
        try:
            path = _safe_project_path(root, raw_path)
        except ValueError as exc:
            _record_check(packet, "evidence-entry-%d" % position, False, str(exc))
            continue
        if not path.is_file():
            _record_check(
                packet,
                "evidence-entry-%d" % position,
                False,
                "missing indexed evidence: %s" % raw_path,
            )
            continue
        if not _SHA256_RE.match(expected_hash):
            _record_check(
                packet,
                "evidence-entry-%d" % position,
                False,
                "invalid SHA-256 for indexed evidence: %s" % raw_path,
            )
            continue
        actual_hash = _sha256(path)
        matches = actual_hash == expected_hash
        _record_check(
            packet,
            "evidence-entry-%d" % position,
            matches,
            "verified indexed evidence: %s" % raw_path
            if matches
            else "evidence hash mismatch: %s" % raw_path,
        )
        if matches:
            by_path[raw_path] = entry
    return index, by_path


def _validate_consistency_assertions(
    packet: Dict[str, Any], root: Path, state: Mapping[str, Any]
) -> None:
    assertions = state.get("consistency_assertions") or []
    if not isinstance(assertions, list) or not assertions:
        _record_check(
            packet,
            "consistency-assertions",
            False,
            "CURRENT_STATE must declare consistency assertions",
        )
        return
    for position, assertion in enumerate(assertions):
        if not isinstance(assertion, Mapping):
            _record_check(
                packet,
                "consistency-%d" % position,
                False,
                "consistency assertion %d must be an object" % position,
            )
            continue
        check_id = str(assertion.get("id") or "consistency-%d" % position)
        raw_path = assertion.get("path")
        try:
            path = _safe_project_path(root, raw_path)
        except ValueError as exc:
            _record_check(packet, check_id, False, str(exc))
            continue
        if not path.is_file():
            _record_check(packet, check_id, False, "assertion source is missing: %s" % raw_path)
            continue
        kind = str(assertion.get("kind") or "")
        try:
            if kind == "text_contains":
                content = path.read_text(encoding="utf-8")
                values = assertion.get("values") or []
                passed = bool(values) and all(str(item) in content for item in values)
                summary = "required declarations present in %s" % raw_path
            elif kind == "text_not_contains":
                content = path.read_text(encoding="utf-8")
                values = assertion.get("values") or []
                passed = bool(values) and all(str(item) not in content for item in values)
                summary = "forbidden stale declarations absent from %s" % raw_path
            elif kind == "json_equals":
                content_json = _read_object(path)
                actual = _json_pointer(content_json, str(assertion.get("pointer") or ""))
                passed = actual == assertion.get("value")
                summary = "JSON invariant matches in %s%s" % (
                    raw_path,
                    assertion.get("pointer") or "",
                )
            else:
                passed = False
                summary = "unsupported consistency assertion kind: %s" % kind
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            passed = False
            summary = "consistency assertion failed to evaluate: %s" % exc
        if not passed and not summary.startswith("unsupported") and "failed to evaluate" not in summary:
            summary = "consistency assertion failed: %s" % check_id
        _record_check(packet, check_id, passed, summary)


def _validate_git(packet: Dict[str, Any], root: Path, state: Mapping[str, Any]) -> None:
    expected = ((state.get("project") or {}).get("git") or {})
    code, actual_root = _git(root, ["rev-parse", "--show-toplevel"])
    root_ok = code == 0 and Path(actual_root).resolve() == root
    _record_check(
        packet,
        "git-root",
        root_ok,
        "selected directory is the exact Git root"
        if root_ok
        else "selected directory is not the exact Git root",
    )
    if not root_ok:
        return
    _code, branch = _git(root, ["branch", "--show-current"])
    _code, head = _git(root, ["rev-parse", "HEAD"])
    expected_branch = str(expected.get("branch") or "")
    branch_ok = not expected_branch or branch == expected_branch
    _record_check(
        packet,
        "git-branch",
        branch_ok,
        "Git branch matches CURRENT_STATE"
        if branch_ok
        else "Git branch mismatch: expected %s, found %s" % (expected_branch, branch),
    )
    baseline = str(expected.get("baseline_commit") or "")
    baseline_ok = bool(_GIT_OID_RE.match(baseline))
    if baseline_ok:
        ancestor_code, _output = _git(root, ["merge-base", "--is-ancestor", baseline, head])
        baseline_ok = ancestor_code == 0
    _record_check(
        packet,
        "git-baseline",
        baseline_ok,
        "current HEAD descends from the declared baseline"
        if baseline_ok
        else "declared baseline is missing, invalid, or not an ancestor of HEAD",
    )
    remote_expected = str(expected.get("remote_url") or "")
    if remote_expected:
        remote_code, remote_actual = _git(root, ["remote", "get-url", "origin"])
        remote_ok = remote_code == 0 and remote_actual == remote_expected
        _record_check(
            packet,
            "git-remote",
            remote_ok,
            "Git remote identity matches CURRENT_STATE"
            if remote_ok
            else "Git remote identity does not match CURRENT_STATE",
        )
    status_code, status_output = _git(root, ["status", "--porcelain"])
    changed_paths = []
    if status_code == 0 and status_output:
        changed_paths = [
            line[3:] if len(line) > 3 else line for line in status_output.splitlines()
        ]
        packet["warnings"].append(
            "working tree has local changes; preserve them and do not assume they belong to the new driver"
        )
    packet["git"] = {
        "branch": branch,
        "head": head,
        "baseline_commit": baseline,
        "working_tree_clean": not changed_paths,
        "changed_paths": changed_paths[:200],
    }


def build_takeover_packet(target: Path) -> Dict[str, Any]:
    """Verify and compile a fresh-driver packet for exactly one selected project.

    The function is read-only.  It never searches sibling repositories and
    never reads conversation history, model credentials, environment secrets,
    or another project's `.ai-delivery` directory.
    """

    root = Path(target).expanduser().resolve()
    packet = _base_packet(root)
    if not root.is_dir():
        _record_check(packet, "selected-project", False, "selected project directory does not exist")
        return packet
    state_path = root / STATE_PATH
    if not state_path.is_file():
        _record_check(
            packet,
            "selected-project",
            False,
            "selected Git project has no .ai-delivery/CURRENT_STATE.json; explicit project initialization is required",
        )
        return packet
    try:
        state = _read_object(state_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _record_check(packet, "current-state", False, "invalid CURRENT_STATE.json: %s" % exc)
        return packet
    project = state.get("project") or {}
    project_id = str(project.get("project_id") or "") if isinstance(project, Mapping) else ""
    header_ok = (
        state.get("schema_version") == CONTINUITY_VERSION
        and state.get("artifact") == "ai_delivery_current_state"
        and bool(project_id)
    )
    packet["project_id"] = project_id
    _record_check(
        packet,
        "current-state-header",
        header_ok,
        "CURRENT_STATE is versioned and project-bound"
        if header_ok
        else "CURRENT_STATE schema, artifact, or project_id is invalid",
    )
    dependency_problems = _scan_for_conversation_dependency(state)
    _record_check(
        packet,
        "conversation-independence",
        not dependency_problems,
        "CURRENT_STATE has no conversation or secret-value dependency"
        if not dependency_problems
        else "; ".join(dependency_problems),
    )
    _validate_isolation(packet, state)
    _validate_git(packet, root, state)
    _index, indexed_by_path = _validate_evidence_index(packet, root, state)

    read_order = state.get("required_read_order") or []
    if not isinstance(read_order, list) or not read_order:
        _record_check(packet, "read-order", False, "CURRENT_STATE has no required_read_order")
    else:
        read_order_ok = True
        for raw_path in read_order:
            try:
                path = _safe_project_path(root, raw_path)
            except ValueError as exc:
                read_order_ok = False
                packet["errors"].append(str(exc))
                continue
            if not path.is_file():
                read_order_ok = False
                packet["errors"].append("required takeover source is missing: %s" % raw_path)
            normalized_path = str(Path(str(raw_path)))
            if normalized_path != str(STATE_PATH) and normalized_path not in indexed_by_path:
                read_order_ok = False
                packet["errors"].append(
                    "required takeover source is not hash-indexed: %s" % raw_path
                )
        _record_check(
            packet,
            "read-order",
            read_order_ok,
            "all required takeover sources exist"
            if read_order_ok
            else "one or more required takeover sources are invalid",
        )

    canonical_sources = state.get("canonical_sources") or []
    canonical_ok = isinstance(canonical_sources, list) and bool(canonical_sources)
    if canonical_ok:
        for source in canonical_sources:
            if not isinstance(source, Mapping):
                canonical_ok = False
                break
            raw_path = str(source.get("path") or "")
            if raw_path != str(STATE_PATH) and raw_path not in indexed_by_path:
                canonical_ok = False
                packet["errors"].append(
                    "canonical source is not hash-indexed: %s" % raw_path
                )
    _record_check(
        packet,
        "canonical-sources",
        canonical_ok,
        "canonical sources are project-local and hash-indexed"
        if canonical_ok
        else "canonical source declaration is missing or incomplete",
    )

    historical_sources = state.get("historical_sources") or []
    historical_ok = isinstance(historical_sources, list)
    if historical_ok:
        for source in historical_sources:
            if not isinstance(source, Mapping):
                historical_ok = False
                break
            raw_path = source.get("path")
            marker = str(source.get("marker") or "")
            try:
                path = _safe_project_path(root, raw_path)
                content = path.read_text(encoding="utf-8")
            except (OSError, ValueError):
                historical_ok = False
                continue
            if not marker or marker not in content:
                historical_ok = False
                packet["errors"].append(
                    "historical source lacks its superseded marker: %s" % raw_path
                )
    _record_check(
        packet,
        "historical-sources",
        historical_ok,
        "historical sources are explicitly marked and cannot impersonate current state"
        if historical_ok
        else "historical source declaration is invalid",
    )
    _validate_consistency_assertions(packet, root, state)

    resume = state.get("resume") or {}
    resume_ok = (
        isinstance(resume, Mapping)
        and isinstance(resume.get("allowed_next_actions"), list)
        and isinstance(resume.get("prohibited_actions"), list)
        and bool(resume.get("prohibited_actions"))
    )
    _record_check(
        packet,
        "resume-boundary",
        resume_ok,
        "resume permissions and prohibitions are explicit"
        if resume_ok
        else "CURRENT_STATE must declare explicit resume permissions and prohibitions",
    )

    cold_start_acceptance = state.get("cold_start_acceptance") or []
    cold_start_ok = isinstance(cold_start_acceptance, list) and bool(
        cold_start_acceptance
    )
    _record_check(
        packet,
        "cold-start-acceptance",
        cold_start_ok,
        "project-specific cold-start acceptance is declared"
        if cold_start_ok
        else "CURRENT_STATE must declare cold-start acceptance expectations",
    )

    packet["project"] = project
    packet["current"] = state.get("current") or {}
    packet["required_read_order"] = read_order
    packet["canonical_sources"] = canonical_sources
    packet["historical_sources"] = historical_sources
    packet["resume"] = resume
    packet["cold_start_acceptance"] = cold_start_acceptance
    packet["isolation"] = state.get("isolation") or {}
    packet["driver_instructions"] = [
        "Bind to project_id %s before reading any product-specific material." % project_id,
        "Read only the selected repository sources in required_read_order.",
        "Treat canonical_sources as current and historical_sources as non-resumable history.",
        "Preserve existing working-tree changes and verify evidence before action.",
        "Do not import product facts, decisions, prompts, cases, or evidence from sibling projects.",
        "Global learning is limited to generic governance policy and anonymized failure patterns.",
        "Stop for the human decisions listed in resume; this packet grants no external action.",
    ]

    if not packet["errors"]:
        packet["ready_for_takeover"] = True
        packet["status"] = "ready_with_warnings" if packet["warnings"] else "ready"
    return packet
