from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


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
}


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
