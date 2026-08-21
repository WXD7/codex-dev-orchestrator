from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional


class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    READY = "ready"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    REVIEW = "review"
    BLOCKED = "blocked"
    FAILED = "failed"
    DONE = "done"


class TaskRole(str, Enum):
    COORDINATOR = "coordinator"
    PLANNER = "planner"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    QA = "qa"


READ_ONLY_ROLES = {
    TaskRole.COORDINATOR.value,
    TaskRole.PLANNER.value,
    TaskRole.REVIEWER.value,
}

WRITE_ROLES = {
    TaskRole.IMPLEMENTER.value,
    TaskRole.QA.value,
}


def normalize_required_artifacts(values: Optional[Iterable[Any]]) -> List[str]:
    """Validate and normalize repository-relative file deliverables.

    Artifact paths cross several trust boundaries (browser, MCP and an agent's
    structured result), so the database uses one strict representation.  This
    deliberately accepts POSIX repository paths only; worktrees are local and
    Git itself presents paths in that form on every supported host.
    """
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        raise ValueError("required_artifacts must be an array of relative paths")

    normalized: List[str] = []
    seen = set()
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError("required_artifacts entries must be strings")
        value = raw.strip()
        if not value:
            raise ValueError("required_artifacts cannot contain an empty path")
        if "\\" in value or "\x00" in value:
            raise ValueError("required_artifacts must use safe POSIX relative paths")
        segments = value.split("/")
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or value.endswith("/")
            or any(segment in ("", ".", "..") for segment in segments)
        ):
            raise ValueError(
                "required_artifacts must contain safe repository-relative file paths"
            )
        canonical = path.as_posix()
        if canonical in seen:
            raise ValueError("required_artifacts contains a duplicate path: %s" % canonical)
        seen.add(canonical)
        normalized.append(canonical)
    return normalized


@dataclass
class AgentRunResult:
    exit_code: int
    status: str
    final: Dict[str, Any]
    session_id: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    stderr_tail: str = ""
    command: List[str] = field(default_factory=list)


@dataclass
class PreflightResult:
    ok: bool
    version: str
    auth_status: str
    problems: List[str] = field(default_factory=list)
