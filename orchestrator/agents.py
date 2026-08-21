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
from .quota import (
    QuotaSnapshot,
    SchedulingDecision,
    choose_model_tier,
    decision_reason,
    quota_mode,
    quota_score,
)


class UnknownExecutor(ValueError):
    pass


class AgentRegistry:
    def __init__(
        self,
        executors: List[AgentExecutor],
        default_name: str = "",
        quota_aware: bool = True,
    ):
        if not executors:
            raise ValueError("At least one executor must be configured")
        self._executors: Dict[str, AgentExecutor] = {
            executor.name: executor for executor in executors
        }
        self.default_name = default_name if default_name in self._executors else executors[0].name
        self.quota_aware = quota_aware
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
        quotas = self.quota_snapshots()
        return {
            "default": self.default_name,
            "quota_aware": self.quota_aware,
            "executors": [
                {
                    "name": name,
                    "label": self._executors[name].label,
                    "ready": result.ok,
                    "version": result.version,
                    "auth_status": result.auth_status,
                    "problems": result.problems,
                    "quota": quotas[name].to_dict(),
                    "recommended_model": self._model_for(
                        name, choose_model_tier(quotas[name], "implementer")
                    ),
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

    # -- quota-aware selection ------------------------------------------

    def quota_snapshot(self, name: str, force: bool = False) -> QuotaSnapshot:
        executor = self.get(name)
        reader = getattr(executor, "quota_snapshot", None)
        if not callable(reader):
            return QuotaSnapshot.unknown(name)
        try:
            snapshot = reader(force=force)
        except TypeError:
            snapshot = reader()
        except Exception as exc:
            return QuotaSnapshot.unknown(name, str(exc) or exc.__class__.__name__)
        return snapshot if isinstance(snapshot, QuotaSnapshot) else QuotaSnapshot.unknown(name)

    def quota_snapshots(self, force: bool = False) -> Dict[str, QuotaSnapshot]:
        return {
            name: self.quota_snapshot(name, force=force)
            if self.check(name).ok
            else QuotaSnapshot.unknown(name, "执行器不可用")
            for name in self.names
        }

    def select(
        self,
        task: Optional[Dict[str, Any]] = None,
        project: Optional[Dict[str, Any]] = None,
    ) -> SchedulingDecision:
        task = task or {}
        project = project or {}
        locked = ""
        locked_reason = ""
        if task.get("session_id") and task.get("assigned_executor") in self._executors:
            locked = str(task["assigned_executor"])
            locked_reason = "沿用现有会话执行器"
        elif str(task.get("executor", "")).strip() in self._executors:
            locked = str(task["executor"]).strip()
            locked_reason = "任务已手动指定执行器"
        elif str(project.get("default_executor", "")).strip() in self._executors:
            locked = str(project["default_executor"]).strip()
            locked_reason = "项目已固定默认执行器"
        elif not self.quota_aware:
            locked = self.default_name
            locked_reason = "额度感知调度已关闭"

        ready = [name for name in self.names if self.check(name).ok]
        candidates = [locked] if locked else ready
        if not candidates:
            candidates = [locked or self.default_name]
        snapshots = {name: self.quota_snapshot(name) for name in candidates}
        if locked:
            selected = locked
            score = quota_score(snapshots[selected], preferred=selected == self.default_name)
        else:
            selected = max(
                candidates,
                key=lambda name: quota_score(
                    snapshots[name], preferred=name == self.default_name
                ),
            )
            score = quota_score(
                snapshots[selected], preferred=selected == self.default_name
            )

        snapshot = snapshots[selected]
        mode = quota_mode(snapshot)
        tier = choose_model_tier(
            snapshot,
            str(task.get("role", "implementer")),
            int(task.get("priority", 50) or 50),
        )
        assigned_model = str(task.get("assigned_model", "")).strip()
        model = (
            assigned_model
            if task.get("session_id") and assigned_model
            else self._model_for(selected, tier)
        )
        reason = decision_reason(snapshot, mode)
        if locked_reason:
            reason = "%s；%s" % (locked_reason, reason)
        return SchedulingDecision(
            executor=selected,
            model=model,
            model_tier=tier,
            mode=mode,
            reason=reason,
            quota=snapshot,
            score=score,
            blocked=mode == "blocked",
            defer_until=snapshot.reset_at if mode == "blocked" else None,
        )

    def _model_for(self, executor: str, tier: str) -> str:
        resolver = getattr(self.get(executor), "model_for", None)
        return str(resolver(tier) if callable(resolver) else "")

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
        model: str = "",
    ) -> AgentRunResult:
        runner = self.get(executor).run
        try:
            return runner(
                run_id, role, worktree, prompt, session_id, on_event, model=model
            )
        except TypeError as exc:
            # Preserve compatibility with a locally supplied executor written
            # for the pre-quota interface. Provider implementations in this
            # project accept the model override directly.
            if "unexpected keyword argument 'model'" not in str(exc):
                raise
            return runner(run_id, role, worktree, prompt, session_id, on_event)


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
            models={
                "high": config.codex_model_high,
                "balanced": config.codex_model_balanced,
                "economy": config.codex_model_economy,
            },
            quota_cache_seconds=config.quota_cache_seconds,
            timeout_seconds=config.run_timeout_seconds,
        ),
        "claude-code": lambda: ClaudeCodeAgent(
            binary=config.claude_binary,
            schema_path=schema_path,
            runs_dir=config.runs_dir,
            model=config.claude_model,
            models={
                "high": config.claude_model_high,
                "balanced": config.claude_model_balanced,
                "economy": config.claude_model_economy,
            },
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
    return AgentRegistry(
        executors, config.default_executor, quota_aware=config.quota_scheduling
    )
