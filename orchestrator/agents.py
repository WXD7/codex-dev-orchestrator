"""Which executor runs a given task.

A task may name an executor, a project may set a default, and the deployment has
a fallback. Heterogeneity is useful precisely because the engineering guarantees
do not move: whichever CLI runs, it gets the same isolated worktree, the same
stripped environment, the same role-scoped write permission and the same
structured result contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent_base import AgentExecutor, EventCallback
from .models import AgentRunResult, PreflightResult


class UnknownExecutor(ValueError):
    pass


class AgentRegistry:
    def __init__(self, executors: List[AgentExecutor], default_name: str = ""):
        if not executors:
            raise ValueError("At least one executor must be configured")
        self._executors: Dict[str, AgentExecutor] = {
            executor.name: executor for executor in executors
        }
        self.default_name = default_name if default_name in self._executors else executors[0].name
        self._preflights: Dict[str, PreflightResult] = {}

    @property
    def names(self) -> List[str]:
        return list(self._executors)

    def get(self, name: str = "") -> AgentExecutor:
        key = (name or "").strip() or self.default_name
        if key not in self._executors:
            raise UnknownExecutor("Unknown executor: %s" % name)
        return self._executors[key]

    def resolve_name(
        self, task: Optional[Dict[str, Any]] = None, project: Optional[Dict[str, Any]] = None
    ) -> str:
        for candidate in (
            (task or {}).get("executor"),
            (project or {}).get("default_executor"),
            self.default_name,
        ):
            key = (candidate or "").strip()
            if key in self._executors:
                return key
        return self.default_name

    # -- readiness --------------------------------------------------------

    def refresh(self) -> Dict[str, PreflightResult]:
        self._preflights = {
            name: executor.preflight() for name, executor in self._executors.items()
        }
        return self._preflights

    def check(self, name: str = "") -> PreflightResult:
        key = (name or "").strip() or self.default_name
        if key not in self._executors:
            return PreflightResult(False, "", "", ["Unknown executor: %s" % name])
        if key not in self._preflights:
            self._preflights[key] = self._executors[key].preflight()
        return self._preflights[key]

    def preflight(self) -> PreflightResult:
        """Ready when at least one executor is usable.

        A machine with Codex installed but not Claude Code must still be able to
        run Codex tasks, so one broken executor never blocks the whole board.
        """
        results = self.refresh()
        usable = [name for name, result in results.items() if result.ok]
        problems: List[str] = []
        for name, result in results.items():
            problems.extend("%s: %s" % (name, problem) for problem in result.problems)
        if not usable:
            return PreflightResult(False, "", "", problems or ["No executor is available"])
        primary = results[self.default_name] if results[self.default_name].ok else results[usable[0]]
        return PreflightResult(
            ok=True,
            version=primary.version,
            auth_status=primary.auth_status,
            problems=problems,
        )

    def health(self) -> Dict[str, Any]:
        results = self._preflights or self.refresh()
        return {
            "default": self.default_name,
            "executors": [
                {
                    "name": name,
                    "label": self._executors[name].label,
                    "ready": result.ok,
                    "version": result.version,
                    "auth_status": result.auth_status,
                    "problems": result.problems,
                }
                for name, result in results.items()
            ],
        }

    def alternate_name(self, exclude: str) -> str:
        """A usable executor that is not `exclude`, or "" when there is none.

        Independent review is only independent if a different model performs it.
        """
        for name, executor in self._executors.items():
            if name != exclude and self.check(name).ok:
                return name
        return ""

    # -- execution --------------------------------------------------------

    def run(
        self,
        run_id: str,
        role: str,
        worktree: Path,
        prompt: str,
        session_id: Optional[str],
        on_event: EventCallback,
        executor: str = "",
    ) -> AgentRunResult:
        return self.get(executor).run(run_id, role, worktree, prompt, session_id, on_event)


def build_registry(config, schema_path: Path) -> AgentRegistry:
    """Instantiate the executors named by ORCH_EXECUTORS."""
    from .claude_agent import ClaudeCodeAgent
    from .codex_agent import CodexAgent

    factories = {
        "codex": lambda: CodexAgent(
            binary=config.codex_binary,
            schema_path=schema_path,
            runs_dir=config.runs_dir,
            model=config.codex_model,
            timeout_seconds=config.run_timeout_seconds,
        ),
        "claude-code": lambda: ClaudeCodeAgent(
            binary=config.claude_binary,
            schema_path=schema_path,
            runs_dir=config.runs_dir,
            model=config.claude_model,
            timeout_seconds=config.run_timeout_seconds,
        ),
    }
    executors: List[AgentExecutor] = []
    for name in config.executors:
        factory = factories.get(name)
        if factory is None:
            raise UnknownExecutor(
                "Unknown executor %r in ORCH_EXECUTORS; known: %s"
                % (name, ", ".join(sorted(factories)))
            )
        executors.append(factory())
    return AgentRegistry(executors, config.default_executor)
