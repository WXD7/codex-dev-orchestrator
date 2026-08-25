"""Recoverable nine-stage delivery loop on top of LobeHub.

LobeHub remains the task, conversation and acceptance source of truth.  The
local journal in this module is deliberately narrower: it is a crash-recovery
log containing stage checkpoints, native object identifiers and redacted gate
summaries.  Every state transition is mirrored to the Task as an idempotent
comment once a Task exists.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .governance import compile_work_contract, route_context
from .lobehub import (
    CODEX_SESSION_MARKER,
    EXECUTION_TOPIC_MARKER,
    LobeHubCLI,
    LobeHubError,
    _created_id,
    _items,
    quota_model_policy,
    run_task_topic_prompt_with_codex,
    sanitized_environment,
    task_topic_is_linked,
)
from .quality import (
    aggregate_findings,
    build_verification_plan,
    decide_release_readiness,
)


RUN_SCHEMA_VERSION = 1
SPEC_SCHEMA_VERSION = 1
RUN_EVENT_MARKER = "[engineering-governance run-event]"
RUN_BINDING_MARKER = "[engineering-governance run]"
STAGES: Tuple[str, ...] = (
    "01_contract_compiled",
    "02_task_bound",
    "03_context_routed",
    "04_quota_planned",
    "05_owner_delivered",
    "06_program_gates_evaluated",
    "07_independent_verification_evaluated",
    "08_single_repair_resolved",
    "09_acceptance_ready",
)
STAGE_TITLES = {
    "01_contract_compiled": "冻结工作契约",
    "02_task_bound": "绑定 LobeHub Task / Topic",
    "03_context_routed": "选择连续上下文",
    "04_quota_planned": "按额度选择模型",
    "05_owner_delivered": "Owner 连续执行",
    "06_program_gates_evaluated": "确定性门禁",
    "07_independent_verification_evaluated": "只读反证监察",
    "08_single_repair_resolved": "一次集中返修",
    "09_acceptance_ready": "准备人工 Acceptance",
}
TERMINAL_RUN_STATUSES = {
    "awaiting_human_acceptance",
    "blocked",
    "completed",
    "canceled",
}
FORBIDDEN_GATE_PROGRAMS = {
    "rm", "rmdir", "mv", "sudo", "ssh", "scp", "rsync", "curl", "wget",
}


class WorkflowPause(RuntimeError):
    """A recoverable policy stop, not an implementation crash."""

    def __init__(self, status: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or {}


class RunJournal:
    """Atomic JSON checkpoint with an advisory single-writer lock."""

    def __init__(self, path: Path, data: Dict[str, Any]):
        self.path = Path(path)
        self.data = data
        self._lock_handle = None

    @classmethod
    def create(
        cls,
        runs_dir: Path,
        spec: Mapping[str, Any],
        run_id: str = "",
    ) -> "RunJournal":
        root = Path(runs_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        selected_id = run_id.strip() or "run_%s" % uuid.uuid4().hex[:16]
        if not re.match(r"^[A-Za-z0-9_-]+$", selected_id):
            raise ValueError("run_id may contain only letters, numbers, '_' and '-'")
        path = root / (selected_id + ".json")
        if path.exists():
            raise ValueError("run already exists: %s" % selected_id)
        now = _timestamp()
        data: Dict[str, Any] = {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": selected_id,
            "status": "created",
            "created_at": now,
            "updated_at": now,
            "spec": dict(spec),
            "resources": {},
            "stages": {
                name: {
                    "title": STAGE_TITLES[name],
                    "status": "pending",
                    "attempts": 0,
                    "output": {},
                }
                for name in STAGES
            },
            "events": [],
            "pause": None,
        }
        journal = cls(path, data)
        journal.save()
        return journal

    @classmethod
    def load(cls, path: Path) -> "RunJournal":
        selected = Path(path).expanduser().resolve()
        with selected.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or data.get("schema_version") != RUN_SCHEMA_VERSION:
            raise ValueError("unsupported or corrupt governed run journal: %s" % selected)
        return cls(selected, data)

    def acquire(self) -> None:
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock_handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_handle.close()
            self._lock_handle = None
            raise RuntimeError("another process is already advancing %s" % self.data["run_id"]) from exc

    def release(self) -> None:
        if self._lock_handle is not None:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = _timestamp()
        descriptor, raw_path = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        temp_path = Path(raw_path)
        try:
            os.chmod(raw_path, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temp_path), str(self.path))
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def append_event(
        self,
        event_type: str,
        stage: str,
        before: str,
        after: str,
        summary: str,
        details: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        sequence = len(self.data["events"]) + 1
        raw_id = "%s:%s:%s:%s" % (
            self.data["run_id"], sequence, event_type, stage
        )
        event = {
            "event_id": "evt_%s" % hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16],
            "sequence": sequence,
            "type": event_type,
            "stage": stage,
            "from": before,
            "to": after,
            "at": _timestamp(),
            "summary": _one_line(_redact_text(summary), 220),
            "details": _safe_event_details(details or {}),
            "lobehub_synced": False,
        }
        self.data["events"].append(event)
        self.save()
        return event


class ProgramGateRunner:
    """Run explicit argv checks without a shell and retain only redacted output."""

    def run(self, gate: Mapping[str, Any], repo: Path) -> Dict[str, Any]:
        gate_id = str(gate.get("id") or "").strip()
        argv = gate.get("argv")
        if not gate_id or not isinstance(argv, list) or not argv:
            raise ValueError("each program gate requires id and non-empty argv")
        command = [str(item) for item in argv]
        _validate_gate_command(command)
        timeout = max(1, min(int(gate.get("timeout", 900) or 900), 3600))
        worktree_before = _worktree_fingerprint(repo)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=str(repo),
                env=sanitized_environment(),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            returncode = completed.returncode
            output = "%s\n%s" % (completed.stdout or "", completed.stderr or "")
            status = "passed" if returncode == 0 else "failed"
            error = ""
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            output = "%s\n%s" % (exc.stdout or "", exc.stderr or "")
            status = "failed"
            error = "timed out after %ss" % timeout
        worktree_after = _worktree_fingerprint(repo)
        worktree_mutated = worktree_before != worktree_after
        if worktree_mutated:
            status = "failed"
            error = (error + "; " if error else "") + "program gate modified the delivery worktree"
        redacted = _redact_text(output)[-8000:]
        return {
            "id": gate_id,
            "status": status,
            "returncode": returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "argv": _redact_argv(command),
            "output_sha256": hashlib.sha256(output.encode("utf-8", "replace")).hexdigest(),
            "redacted_output_tail": redacted,
            "error": error,
            "worktree_mutated": worktree_mutated,
            "acceptance_check": False,
        }


class GovernedTaskLoop:
    """Advance and recover the nine governance stages."""

    def __init__(
        self,
        journal: RunJournal,
        client: Optional[LobeHubCLI] = None,
        quota_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        turn_executor: Optional[Callable[..., Dict[str, Any]]] = None,
        gate_runner: Optional[ProgramGateRunner] = None,
        timeout: int = 3600,
    ):
        self.journal = journal
        self.client = client or LobeHubCLI()
        self.quota_provider = quota_provider or quota_model_policy
        self.turn_executor = turn_executor or run_task_topic_prompt_with_codex
        self.gate_runner = gate_runner or ProgramGateRunner()
        self.timeout = max(1, int(timeout))
        self.spec = _validate_spec(self.journal.data["spec"])
        self.repo = Path(self.spec["repo"]).expanduser().resolve()

    def run(self, stop_after: str = "") -> Dict[str, Any]:
        if stop_after and stop_after not in STAGES:
            raise ValueError("stop_after must be one of: %s" % ", ".join(STAGES))
        self.journal.acquire()
        try:
            if self.journal.data.get("status") == "awaiting_human_acceptance":
                return self.summary()
            self._begin_or_resume()
            try:
                self._flush_events()
            except Exception as exc:
                return self._mark_interrupted(self._current_stage(), exc)
            handlers = {
                STAGES[0]: self._compile_contract,
                STAGES[1]: self._bind_task,
                STAGES[2]: self._route_owner_context,
                STAGES[3]: self._plan_quota,
                STAGES[4]: self._run_owner,
                STAGES[5]: self._run_program_gates,
                STAGES[6]: self._run_initial_verification,
                STAGES[7]: self._resolve_repair,
                STAGES[8]: self._prepare_acceptance,
            }
            for stage in STAGES:
                record = self.journal.data["stages"][stage]
                if record["status"] in {"completed", "skipped"}:
                    continue
                try:
                    self._transition_stage(stage, "running", "开始：%s" % STAGE_TITLES[stage])
                except Exception as exc:
                    return self._mark_interrupted(stage, exc)
                try:
                    output = handlers[stage]() or {}
                    final_status = str(output.pop("_stage_status", "completed"))
                    if final_status not in {"completed", "skipped"}:
                        raise ValueError("invalid terminal stage status: %s" % final_status)
                    # Stage handlers checkpoint sub-operations (message ids,
                    # turns, verifier topics) before performing their side
                    # effects. Preserve those recovery keys when storing the
                    # final stage summary.
                    record.setdefault("output", {}).update(output)
                    self._transition_stage(
                        stage,
                        final_status,
                        output.get("summary") or "完成：%s" % STAGE_TITLES[stage],
                    )
                except WorkflowPause as pause:
                    record["output"].update(pause.details)
                    self.journal.data["status"] = pause.status
                    self.journal.data["pause"] = {
                        "status": pause.status,
                        "message": pause.message,
                        "stage": stage,
                        "at": _timestamp(),
                    }
                    try:
                        self._transition_stage(stage, "waiting", pause.message)
                    except Exception as exc:
                        return self._mark_interrupted(stage, exc)
                    try:
                        self._set_task_status("paused")
                    except Exception as exc:
                        return self._mark_interrupted(stage, exc)
                    return self.summary()
                except Exception as exc:
                    return self._mark_interrupted(stage, exc)
                if stop_after == stage:
                    self.journal.data["status"] = "checkpointed"
                    self.journal.data["pause"] = {
                        "status": "checkpointed",
                        "message": "Stopped after requested recovery checkpoint.",
                        "stage": stage,
                        "at": _timestamp(),
                    }
                    self.journal.append_event(
                        "run_checkpointed", stage, "running", "checkpointed",
                        "按请求在安全检查点停止。",
                    )
                    try:
                        self._flush_events()
                        self._set_task_status("paused")
                    except Exception as exc:
                        return self._mark_interrupted(stage, exc)
                    return self.summary()
            self.journal.data["status"] = "awaiting_human_acceptance"
            self.journal.data["pause"] = {
                "status": "awaiting_human_acceptance",
                "message": "九个阶段已完成；等待人在 LobeHub Acceptance 做最终决定。",
                "stage": STAGES[-1],
                "at": _timestamp(),
            }
            self.journal.append_event(
                "run_ready", STAGES[-1], "running", "awaiting_human_acceptance",
                "九步自动循环完成，未模拟最终人工验收。",
            )
            try:
                self._flush_events()
                self._set_task_status("paused")
            except Exception as exc:
                return self._mark_interrupted(STAGES[-1], exc)
            return self.summary()
        finally:
            self.journal.release()

    def _mark_interrupted(self, stage: str, exc: Exception) -> Dict[str, Any]:
        record = self.journal.data["stages"][stage]
        before = str(record.get("status") or "pending")
        if before not in {"completed", "skipped"}:
            record["status"] = "interrupted"
        self.journal.data["status"] = "interrupted"
        self.journal.data["pause"] = {
            "status": "interrupted",
            "message": _redact_text(str(exc)),
            "stage": stage,
            "at": _timestamp(),
        }
        self.journal.append_event(
            "stage_transition", stage, before, "interrupted", _redact_text(str(exc)),
            {"attempt": record.get("attempts", 0)},
        )
        try:
            self._flush_events()
        except Exception:
            # The unsynced event remains durable and will be reconciled before
            # any side effect on the next resume.
            pass
        try:
            self._set_task_status("paused", check=False)
        except Exception:
            pass
        return self.summary()

    def summary(self) -> Dict[str, Any]:
        current = next(
            (name for name in STAGES if self.journal.data["stages"][name]["status"] not in {"completed", "skipped"}),
            STAGES[-1],
        )
        return {
            "run_id": self.journal.data["run_id"],
            "status": self.journal.data["status"],
            "current_stage": current,
            "task_id": self.journal.data["resources"].get("task_id"),
            "owner_topic_id": self.journal.data["resources"].get("owner_topic_id"),
            "repair_rounds_used": self.journal.data["resources"].get("repair_rounds_used", 0),
            "journal": str(self.journal.path),
            "pause": self.journal.data.get("pause"),
            "stages": {
                name: self.journal.data["stages"][name]["status"] for name in STAGES
            },
        }

    def _begin_or_resume(self) -> None:
        previous = str(self.journal.data.get("status") or "created")
        if previous in TERMINAL_RUN_STATUSES:
            if previous == "awaiting_human_acceptance":
                return
            raise RuntimeError("run is terminal and cannot be resumed: %s" % previous)
        self.journal.data["status"] = "running"
        self.journal.data["pause"] = None
        self.journal.append_event(
            "run_started" if previous == "created" else "run_resumed",
            self._current_stage(), previous, "running",
            "启动九步控制循环。" if previous == "created" else "从最近检查点恢复控制循环。",
        )

    def _current_stage(self) -> str:
        for name in STAGES:
            if self.journal.data["stages"][name]["status"] not in {"completed", "skipped"}:
                return name
        return STAGES[-1]

    def _transition_stage(self, stage: str, target: str, summary: str) -> None:
        record = self.journal.data["stages"][stage]
        before = str(record.get("status") or "pending")
        if target == "running" and before != "running":
            record["attempts"] = int(record.get("attempts", 0)) + 1
            if before in {"waiting", "interrupted"}:
                self.journal.append_event(
                    "stage_recovered", stage, before, "pending",
                    "恢复未完成阶段；已完成阶段不会重跑。",
                )
                before = "pending"
        record["status"] = target
        self.journal.append_event(
            "stage_transition", stage, before, target, summary,
            {"attempt": record.get("attempts", 0)},
        )
        self._flush_events()

    def _checkpoint(self, stage: str, key: str, value: Any) -> None:
        output = self.journal.data["stages"][stage].setdefault("output", {})
        output[key] = value
        self.journal.save()

    def _resource(self, key: str, value: Any) -> None:
        self.journal.data["resources"][key] = value
        self.journal.save()

    def _flush_events(self) -> None:
        task_id = str(self.journal.data["resources"].get("task_id") or "")
        if not task_id:
            return
        task = self.client.json(["task", "view", task_id])
        if not isinstance(task, dict):
            raise LobeHubError("could not reconcile run events with Task %s" % task_id)
        activity_text = "\n".join(
            str(item.get("content") or "")
            for item in task.get("activities") or []
            if isinstance(item, dict)
        )
        changed = False
        for event in self.journal.data["events"]:
            if event.get("lobehub_synced"):
                continue
            event_id = str(event["event_id"])
            if event_id not in activity_text:
                payload = {
                    "run_id": self.journal.data["run_id"],
                    "event_id": event_id,
                    "seq": event["sequence"],
                    "stage": event["stage"],
                    "from": event["from"],
                    "to": event["to"],
                    "at": event["at"],
                    "summary": event["summary"],
                }
                self.client.run(
                    [
                        "task", "comment", task_id, "--message",
                        RUN_EVENT_MARKER + " " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    ],
                    check=True,
                )
                activity_text += "\n" + event_id
            event["lobehub_synced"] = True
            changed = True
        if changed:
            self.journal.save()

    def _set_task_status(self, status: str, check: bool = True) -> None:
        task_id = str(self.journal.data["resources"].get("task_id") or "")
        if task_id:
            self.client.run(
                ["task", "edit", task_id, "--status", status], check=check
            )

    def _compile_contract(self) -> Dict[str, Any]:
        contract = compile_work_contract(dict(self.spec["goal"]))
        self._checkpoint(STAGES[0], "contract", contract)
        if contract["status"] != "ready":
            raise WorkflowPause(
                "needs_clarification",
                "工作契约缺少可观察结果，材料执行未开始。",
                {"clarification_questions": contract["clarification_questions"]},
            )
        return {
            "contract": contract,
            "summary": "工作契约已冻结：%s" % contract["contract_hash"][:12],
        }

    def _contract(self) -> Dict[str, Any]:
        return dict(self.journal.data["stages"][STAGES[0]]["output"]["contract"])

    def _bind_task(self) -> Dict[str, Any]:
        contract = self._contract()
        resources = self.journal.data["resources"]
        task_spec = self.spec.get("task") or {}
        task_id = str(resources.get("task_id") or task_spec.get("id") or "").strip()
        if task_id:
            task = self.client.json(["task", "view", task_id])
            if not isinstance(task, dict):
                raise LobeHubError("existing Task was not found: %s" % task_id)
            instruction = str(task.get("instruction") or "")
            if contract["contract_hash"] not in instruction:
                raise WorkflowPause(
                    "needs_human_decision",
                    "现有 Task 的冻结契约哈希与本次运行不一致。",
                    {"expected_contract_hash": contract["contract_hash"]},
                )
            self._resource("task_id", str(task.get("id") or task_id))
        else:
            task_id = self._find_or_create_task(contract)
            task = self.client.json(["task", "view", task_id])

        topic_id = str(resources.get("owner_topic_id") or task_spec.get("topic_id") or "").strip()
        if not topic_id:
            topic_id = self._find_or_create_topic(
                "%s [gov:%s]" % (self.spec["name"], self.journal.data["run_id"][-8:])
            )
        self._resource("owner_topic_id", topic_id)
        task = self.client.json(["task", "view", task_id])
        if not task_topic_is_linked(task, task_id, topic_id, self.client):
            self.client.run(
                ["task", "comment", task_id, "--message", "%s %s" % (EXECUTION_TOPIC_MARKER, topic_id)],
                check=True,
            )
        messages = _items(self.client.json(["task", "topic", "view", task_id, topic_id]))
        contract_message = next(
            (
                item for item in messages
                if str(item.get("role") or "") == "user"
                and contract["contract_hash"] in str(item.get("content") or "")
            ),
            None,
        )
        if contract_message:
            contract_message_id = str(contract_message.get("id") or "")
        else:
            created = self.client.json(
                [
                    "message", "create", "--role", "user", "--content",
                    contract["task_instruction"], "--topic-id", topic_id,
                ]
            )
            contract_message_id = str(created.get("id") or "") if isinstance(created, dict) else ""
            if not contract_message_id:
                raise LobeHubError("could not persist frozen contract message")
        self._resource("contract_message_id", contract_message_id)
        self._set_task_status("running")
        return {
            "task_id": task_id,
            "topic_id": topic_id,
            "contract_message_id": contract_message_id,
            "summary": "已绑定 LobeHub Task %s 与 Owner Topic %s。" % (task_id, topic_id),
        }

    def _find_or_create_task(self, contract: Mapping[str, Any]) -> str:
        marker = "%s %s contract %s" % (
            RUN_BINDING_MARKER, self.journal.data["run_id"], contract["contract_hash"]
        )
        for item in _items(self.client.json(["task", "list", "--limit", "100"])):
            searchable = "%s\n%s" % (
                item.get("description") or "", item.get("instruction") or ""
            )
            if marker in searchable:
                task_id = str(item.get("id") or "")
                if task_id:
                    self._resource("task_id", task_id)
                    return task_id
        result = self.client.run(
            [
                "project", "task", "create", self.spec["project_id"],
                "--name", self.spec["name"],
                "--instruction", contract["task_instruction"] + "\n\nCONTROL RUN\n" + marker,
            ],
            check=True,
        )
        task_id = _created_id(result.stdout)
        self._resource("task_id", task_id)
        settings = contract["lobe_settings"]
        self.client.run(
            [
                "task", "checkpoint", "set", task_id,
                "--on-agent-request", "true",
                "--topic-before", str(settings["pause_before_each_topic"]).lower(),
                "--topic-after", str(settings["pause_after_each_topic"]).lower(),
            ],
            check=True,
        )
        return task_id

    def _find_or_create_topic(self, title: str) -> str:
        for item in _items(self.client.json(["topic", "list", "--limit", "100"])):
            if str(item.get("title") or "") == title:
                topic_id = str(item.get("id") or "")
                if topic_id:
                    return topic_id
        result = self.client.run(["topic", "create", "--title", title], check=True)
        return _created_id(result.stdout)

    def _route_owner_context(self) -> Dict[str, Any]:
        resources = self.journal.data["resources"]
        owner_topic = str(resources["owner_topic_id"])
        candidates = list(self.spec.get("context_candidates") or [])
        if not any(str(item.get("topic_id") or item.get("id") or "") == owner_topic for item in candidates if isinstance(item, dict)):
            candidates.append(
                {
                    "topic_id": owner_topic,
                    "project_id": self.spec.get("project_id", ""),
                    "repo_path": str(self.repo),
                    "status": "paused",
                    "title": self.spec["name"],
                    "summary": self._contract()["goal"],
                }
            )
        decision = route_context(
            {
                "work": self._contract()["goal"],
                "purpose": "delivery",
                "project_id": self.spec.get("project_id", ""),
                "repo_path": str(self.repo),
                "touched_paths": self.spec.get("touched_paths") or [],
                "candidates": candidates,
            }
        )
        selected_topic = owner_topic
        selected_session = ""
        if decision["decision"] == "continue_existing":
            selected_topic = str(decision["topic_id"])
            selected_session = str(decision.get("codex_session_id") or "")
        task_id = str(resources["task_id"])
        task = self.client.json(["task", "view", task_id])
        if not task_topic_is_linked(task, task_id, selected_topic, self.client):
            self.client.run(
                ["task", "comment", task_id, "--message", "%s %s" % (EXECUTION_TOPIC_MARKER, selected_topic)],
                check=True,
            )
        selected_session = selected_session or self._session_for_topic(task, selected_topic)
        self._resource("owner_topic_id", selected_topic)
        self._resource("owner_session_id", selected_session or None)
        return {
            "decision": decision,
            "selected_topic_id": selected_topic,
            "selected_session_id": selected_session or None,
            "summary": "Owner 上下文：%s（%s）。" % (selected_topic, decision["decision"]),
        }

    @staticmethod
    def _session_for_topic(task: Mapping[str, Any], topic_id: str) -> str:
        for activity in reversed(list(task.get("activities") or [])):
            if not isinstance(activity, dict):
                continue
            content = str(activity.get("content") or "")
            if CODEX_SESSION_MARKER not in content or topic_id not in content.split():
                continue
            match = re.search(
                re.escape(CODEX_SESSION_MARKER)
                + r"\s+(\S+)\s+topic\s+"
                + re.escape(topic_id)
                + r"(?:\s|$)",
                content,
            )
            if match:
                return match.group(1)
        return ""

    def _plan_quota(self) -> Dict[str, Any]:
        policy = dict(self.quota_provider())
        if policy.get("mode") == "blocked":
            raise WorkflowPause(
                "quota_deferred",
                "Codex 订阅额度已触顶，等待额度窗口刷新后恢复。",
                {"defer_until": policy.get("defer_until"), "quota_policy": policy},
            )
        overrides = self.spec.get("models") or {}
        models = dict(policy.get("models") or {})
        for role in ("owner", "verifier"):
            if str(overrides.get(role) or "").strip():
                models[role] = str(overrides[role]).strip()
        if not models.get("owner") or not models.get("verifier"):
            raise ValueError("quota policy must select owner and verifier models")
        policy["models"] = models
        self._resource("models", models)
        return {
            "policy": policy,
            "summary": "额度模式 %s；Owner=%s，Verifier=%s。" % (
                policy.get("mode", "unknown"), models["owner"], models["verifier"]
            ),
        }

    def _run_owner(self) -> Dict[str, Any]:
        contract = self._contract()
        if contract.get("requires_immediate_human_decision") and not bool(
            (self.spec.get("decisions") or {}).get("material_execution_approved")
        ):
            raise WorkflowPause(
                "needs_human_decision",
                "该契约需要人在材料执行前确认风险边界。",
                {"decision_required": "material_execution_approved"},
            )
        result = self._execute_governed_turn(
            stage=STAGES[4],
            turn_key="owner_delivery",
            topic_id=str(self.journal.data["resources"]["owner_topic_id"]),
            prompt=contract["task_instruction"],
            sandbox="workspace-write",
            model=str(self.journal.data["resources"]["models"]["owner"]),
            session_id=str(self.journal.data["resources"].get("owner_session_id") or ""),
        )
        session_id = str(result.get("continuation_session_id") or "")
        if session_id:
            self._resource("owner_session_id", session_id)
        return {
            "assistant_message_id": result.get("assistant_message_id"),
            "continuation_session_id": session_id or None,
            "event_count": result.get("event_count", 0),
            "summary": "Owner 已完成首轮交付，并保存可续跑 Session。",
        }

    def _execute_governed_turn(
        self,
        stage: str,
        turn_key: str,
        topic_id: str,
        prompt: str,
        sandbox: str,
        model: str,
        session_id: str = "",
    ) -> Dict[str, Any]:
        turns = self.journal.data["stages"][stage].setdefault("output", {}).setdefault("turns", {})
        turn = turns.setdefault(turn_key, {})
        task_id = str(self.journal.data["resources"]["task_id"])
        if turn.get("assistant_message_id"):
            recovered = self._recover_visible_turn(task_id, topic_id, str(turn["assistant_message_id"]))
            if recovered:
                final_text = str(recovered.pop("final_text"))
                turn.update(recovered)
                turn["final_text_sha256"] = hashlib.sha256(final_text.encode("utf-8")).hexdigest()
                turn["final_text_chars"] = len(final_text)
                self.journal.save()
                response = dict(turn)
                response["final_text"] = final_text
                return response
        if not turn.get("prompt_message_id"):
            created = self.client.json(
                ["message", "create", "--role", "user", "--content", prompt, "--topic-id", topic_id]
            )
            turn["prompt_message_id"] = str(created.get("id") or "") if isinstance(created, dict) else ""
            if not turn["prompt_message_id"]:
                raise LobeHubError("could not persist governed turn prompt")
            self.journal.save()
        if not turn.get("assistant_message_id"):
            created = self.client.json(
                ["message", "create", "--role", "assistant", "--content", "...", "--topic-id", topic_id]
            )
            turn["assistant_message_id"] = str(created.get("id") or "") if isinstance(created, dict) else ""
            if not turn["assistant_message_id"]:
                raise LobeHubError("could not create governed turn placeholder")
            self.journal.save()
        operation_id = "op_gov_%s_%s_%s" % (
            re.sub(r"[^A-Za-z0-9_]", "_", self.journal.data["run_id"]),
            re.sub(r"[^A-Za-z0-9_]", "_", turn_key)[:40],
            self.journal.data["stages"][stage].get("attempts", 1),
        )
        turn["operation_id"] = operation_id
        self.journal.save()
        result = self.turn_executor(
            task_id=task_id,
            topic_id=topic_id,
            cwd=self.repo,
            prompt=prompt,
            sandbox=sandbox,
            model=model,
            operation_id=operation_id,
            session_id=session_id,
            assistant_message_id=str(turn["assistant_message_id"]),
            client=self.client,
            timeout=self.timeout,
            persist_prompt=False,
            manage_task_status=False,
        )
        final_text = str(result.get("final_text") or "")
        turn.update(
            {
                "completed": True,
                "continuation_session_id": result.get("continuation_session_id"),
                "event_count": result.get("event_count", 0),
                "final_text_sha256": hashlib.sha256(final_text.encode("utf-8")).hexdigest(),
                "final_text_chars": len(final_text),
            }
        )
        self.journal.save()
        response = dict(turn)
        response["final_text"] = final_text
        return response

    def _recover_visible_turn(self, task_id: str, topic_id: str, message_id: str) -> Dict[str, Any]:
        messages = _items(self.client.json(["task", "topic", "view", task_id, topic_id]))
        for message in messages:
            if str(message.get("id") or "") != message_id:
                continue
            content = str(message.get("content") or "").strip()
            if content and content != "...":
                task = self.client.json(["task", "view", task_id])
                return {
                    "completed": True,
                    "recovered_from_lobehub": True,
                    "final_text": content,
                    "continuation_session_id": self._session_for_topic(task, topic_id),
                }
        return {}

    def _program_gate_specs(self) -> List[Dict[str, Any]]:
        explicit = self.spec.get("program_gates") or []
        gates = [dict(item) for item in explicit if isinstance(item, dict)]
        if not gates:
            gates = discover_program_gates(self.repo)
        if not any(str(item.get("id") or "") == "changeset_integrity" for item in gates):
            gates.append(
                {"id": "changeset_integrity", "argv": ["git", "diff", "--check"], "timeout": 120}
            )
        return gates

    def _evaluate_program_gates(self) -> List[Dict[str, Any]]:
        gates = self._program_gate_specs()
        if len(gates) == 1 and gates[0]["id"] == "changeset_integrity":
            raise WorkflowPause(
                "needs_configuration",
                "仓库没有可识别的测试门禁；请在运行 spec 中声明 program_gates。",
                {"discovered_gates": gates},
            )
        return [self.gate_runner.run(gate, self.repo) for gate in gates]

    def _run_program_gates(self) -> Dict[str, Any]:
        results = self._evaluate_program_gates()
        passed = all(item["status"] == "passed" for item in results)
        return {
            "passed": passed,
            "results": results,
            "summary": "确定性门禁 %s（%d/%d 通过）。" % (
                "通过" if passed else "发现失败", sum(item["status"] == "passed" for item in results), len(results)
            ),
        }

    def _run_initial_verification(self) -> Dict[str, Any]:
        gate_output = self.journal.data["stages"][STAGES[5]]["output"]
        plan = build_verification_plan({"contract": self._contract()})
        if not gate_output.get("passed"):
            aggregate = aggregate_findings(
                {
                    "program_gates": gate_output.get("results") or [],
                    "required_lanes": [],
                    "completed_lanes": [],
                    "findings": [],
                    "repair_rounds_used": 0,
                }
            )
            return {
                "plan": plan,
                "lanes": [],
                "aggregate": aggregate,
                "summary": "程序门禁未通过，跳过昂贵监察并进入一次集中返修。",
                "_stage_status": "skipped",
            }
        round_result = self._run_verification_round(plan, 0, STAGES[6])
        aggregate = aggregate_findings(
            {
                "program_gates": gate_output.get("results") or [],
                "required_lanes": round_result["required_lanes"],
                "completed_lanes": round_result["completed_lanes"],
                "findings": round_result["findings"],
                "repair_rounds_used": 0,
                "human_gate_required": self._contract().get("requires_immediate_human_decision"),
            }
        )
        return {
            "plan": plan,
            "lanes": round_result["lanes"],
            "aggregate": aggregate,
            "summary": "只读监察完成，聚合决策：%s。" % aggregate["decision"],
        }

    def _run_verification_round(self, plan: Mapping[str, Any], round_index: int, stage: str) -> Dict[str, Any]:
        lanes = list(plan.get("independent_lanes") or [])
        required = [str(lane["id"]) for lane in lanes if lane.get("blocking", True)]
        completed: List[str] = []
        findings: List[Dict[str, Any]] = []
        lane_results: List[Dict[str, Any]] = []
        for lane in lanes:
            lane_id = str(lane["id"])
            fresh = bool(lane.get("fresh_context", True))
            if fresh:
                topic_id = self._verification_topic(stage, lane_id, round_index)
                session_id = ""
            else:
                topic_id = str(self.journal.data["resources"]["owner_topic_id"])
                session_id = str(self.journal.data["resources"].get("owner_session_id") or "")
            prompt = _verification_prompt(self._contract(), lane, round_index)
            turn = self._execute_governed_turn(
                stage=stage,
                turn_key="verify_r%d_%s" % (round_index, lane_id),
                topic_id=topic_id,
                prompt=prompt,
                sandbox="read-only",
                model=str(self.journal.data["resources"]["models"]["verifier"]),
                session_id=session_id,
            )
            parsed = parse_verifier_result(str(turn.get("final_text") or ""), lane_id)
            parsed["topic_id"] = topic_id
            parsed["fresh_context"] = fresh
            lane_results.append(parsed)
            if parsed["parsed"] and parsed["status"] in {"passed", "findings"}:
                completed.append(lane_id)
                for finding in parsed["findings"]:
                    item = dict(finding)
                    item["source"] = lane_id
                    findings.append(item)
        return {
            "required_lanes": required,
            "completed_lanes": completed,
            "findings": findings,
            "lanes": lane_results,
        }

    def _verification_topic(self, stage: str, lane_id: str, round_index: int) -> str:
        key = "verification_topics"
        topics = self.journal.data["stages"][stage].setdefault("output", {}).setdefault(key, {})
        topic_key = "r%d_%s" % (round_index, lane_id)
        if topics.get(topic_key):
            return str(topics[topic_key])
        title = "[gov:%s] verify r%d %s" % (
            self.journal.data["run_id"][-8:], round_index, lane_id
        )
        topic_id = self._find_or_create_topic(title)
        topics[topic_key] = topic_id
        self.journal.save()
        task_id = str(self.journal.data["resources"]["task_id"])
        task = self.client.json(["task", "view", task_id])
        if not task_topic_is_linked(task, task_id, topic_id, self.client):
            self.client.run(
                ["task", "comment", task_id, "--message", "%s %s" % (EXECUTION_TOPIC_MARKER, topic_id)],
                check=True,
            )
        return topic_id

    def _resolve_repair(self) -> Dict[str, Any]:
        initial = self.journal.data["stages"][STAGES[6]]["output"].get("aggregate") or {}
        decision = str(initial.get("decision") or "")
        if decision in {"pass", "ready_for_human_acceptance"}:
            return {
                "repair_rounds_used": 0,
                "aggregate": initial,
                "summary": "没有阻断发现，不启动返修。",
                "_stage_status": "skipped",
            }
        if decision != "repair_once":
            raise WorkflowPause(
                "needs_human_decision",
                "监察证据缺失、冲突或不可自动判断，未擅自返修。",
                {"aggregate": initial},
            )
        stage_output = self.journal.data["stages"][STAGES[7]].setdefault("output", {})
        if int(self.journal.data["resources"].get("repair_rounds_used", 0)) == 0:
            self._resource("repair_rounds_used", 1)
        repair_prompt = _repair_prompt(self._contract(), initial)
        result = self._execute_governed_turn(
            stage=STAGES[7],
            turn_key="consolidated_repair_1",
            topic_id=str(self.journal.data["resources"]["owner_topic_id"]),
            prompt=repair_prompt,
            sandbox="workspace-write",
            model=str(self.journal.data["resources"]["models"]["owner"]),
            session_id=str(self.journal.data["resources"].get("owner_session_id") or ""),
        )
        if result.get("continuation_session_id"):
            self._resource("owner_session_id", result["continuation_session_id"])
        if not stage_output.get("post_repair_gates"):
            stage_output["post_repair_gates"] = self._evaluate_program_gates()
            self.journal.save()
        gates = stage_output["post_repair_gates"]
        if not all(item.get("status") == "passed" for item in gates):
            aggregate = aggregate_findings(
                {
                    "program_gates": gates,
                    "required_lanes": [],
                    "completed_lanes": [],
                    "findings": [],
                    "repair_rounds_used": 1,
                }
            )
            stage_output["aggregate"] = aggregate
            self.journal.save()
            raise WorkflowPause(
                "needs_human_decision",
                "唯一一次自动返修后程序门禁仍失败，已升级给人。",
                {"aggregate": aggregate, "repair_rounds_used": 1},
            )
        plan = self.journal.data["stages"][STAGES[6]]["output"].get("plan") or build_verification_plan({"contract": self._contract()})
        if not stage_output.get("post_repair_verification"):
            stage_output["post_repair_verification"] = self._run_verification_round(plan, 1, STAGES[7])
            self.journal.save()
        verification = stage_output["post_repair_verification"]
        aggregate = aggregate_findings(
            {
                "program_gates": gates,
                "required_lanes": verification["required_lanes"],
                "completed_lanes": verification["completed_lanes"],
                "findings": verification["findings"],
                "repair_rounds_used": 1,
                "human_gate_required": self._contract().get("requires_immediate_human_decision"),
            }
        )
        stage_output["aggregate"] = aggregate
        self.journal.save()
        if aggregate["decision"] not in {"pass", "ready_for_human_acceptance"}:
            raise WorkflowPause(
                "needs_human_decision",
                "唯一一次自动返修后的监察仍未通过，已升级给人。",
                {"aggregate": aggregate, "repair_rounds_used": 1},
            )
        return {
            "repair_rounds_used": 1,
            "post_repair_gates": gates,
            "post_repair_verification": verification,
            "aggregate": aggregate,
            "summary": "一次集中返修及完整回归通过。",
        }

    def _final_aggregate(self) -> Dict[str, Any]:
        repair = self.journal.data["stages"][STAGES[7]]["output"]
        if repair.get("aggregate"):
            return dict(repair["aggregate"])
        return dict(self.journal.data["stages"][STAGES[6]]["output"].get("aggregate") or {})

    def _prepare_acceptance(self) -> Dict[str, Any]:
        aggregate = self._final_aggregate()
        readiness = decide_release_readiness(
            {
                "contract": self._contract(),
                "verification": aggregate,
                "human_approved": False,
                "release_requested": False,
            }
        )
        if readiness["status"] == "blocked":
            raise WorkflowPause(
                "needs_human_decision",
                "发布就绪判断仍有阻断项。",
                {"readiness": readiness},
            )
        handoff = {
            "status": "ready_for_human_acceptance",
            "task_id": self.journal.data["resources"]["task_id"],
            "contract_hash": self._contract()["contract_hash"],
            "acceptance_criteria": self._contract()["acceptance_criteria"],
            "program_gates_are_preconditions": True,
            "repair_rounds_used": self.journal.data["resources"].get("repair_rounds_used", 0),
            "human_accept_or_reject_simulated": False,
            "external_action_performed": False,
        }
        return {
            "readiness": readiness,
            "handoff": handoff,
            "summary": "交付已到人工 Acceptance 边界；未自动合并、发布或代替人验收。",
        }


def discover_program_gates(repo: Path) -> List[Dict[str, Any]]:
    """Discover conservative repository-defined checks; never use a shell."""

    root = Path(repo)
    result: List[Dict[str, Any]] = []
    if (root / "pyproject.toml").is_file() and (root / "tests").is_dir():
        result.append(
            {
                "id": "python_unittest",
                "argv": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                "timeout": 1800,
            }
        )
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            package = {}
        scripts = package.get("scripts") if isinstance(package, dict) else {}
        if isinstance(scripts, dict):
            for name in ("test", "lint", "typecheck", "build"):
                value = str(scripts.get(name) or "").strip()
                if value and "no test specified" not in value.lower():
                    result.append(
                        {"id": "npm_%s" % name, "argv": ["npm", "run", name], "timeout": 1800}
                    )
    if (root / "Cargo.toml").is_file():
        result.append({"id": "cargo_test", "argv": ["cargo", "test"], "timeout": 1800})
    if (root / "go.mod").is_file():
        result.append({"id": "go_test", "argv": ["go", "test", "./..."], "timeout": 1800})
    return result


def parse_verifier_result(text: str, lane_id: str) -> Dict[str, Any]:
    candidates: List[str] = []
    tagged = re.search(r"<governance-findings>\s*(.*?)\s*</governance-findings>", text, re.DOTALL)
    if tagged:
        candidates.append(tagged.group(1))
    candidates.extend(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL))
    candidates.append(text.strip())
    payload: Optional[Dict[str, Any]] = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            value = _first_json_object(candidate)
        if isinstance(value, dict):
            payload = value
            break
    if payload is None:
        return {
            "lane_id": lane_id,
            "status": "blocked",
            "parsed": False,
            "findings": [],
            "error": "verifier did not return the required structured finding envelope",
        }
    returned_lane = str(payload.get("lane_id") or lane_id)
    status = str(payload.get("status") or "blocked").lower()
    findings = payload.get("findings")
    if returned_lane != lane_id or status not in {"passed", "findings", "blocked"} or not isinstance(findings, list):
        return {
            "lane_id": lane_id,
            "status": "blocked",
            "parsed": False,
            "findings": [],
            "error": "verifier envelope failed schema validation",
        }
    return {
        "lane_id": lane_id,
        "status": status,
        "parsed": True,
        "findings": [item for item in findings if isinstance(item, dict)],
        "error": str(payload.get("error") or ""),
    }


def load_spec(path: Path) -> Dict[str, Any]:
    with Path(path).expanduser().resolve().open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("governed task spec must be a JSON object")
    return _validate_spec(payload)


def resolve_run_path(value: str, runs_dir: Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    name = value if value.endswith(".json") else value + ".json"
    path = Path(runs_dir).expanduser().resolve() / name
    if not path.is_file():
        raise ValueError("governed run journal was not found: %s" % value)
    return path


def _validate_spec(payload: Mapping[str, Any]) -> Dict[str, Any]:
    spec = dict(payload)
    if int(spec.get("schema_version", SPEC_SCHEMA_VERSION)) != SPEC_SCHEMA_VERSION:
        raise ValueError("unsupported governed task spec schema_version")
    repo = Path(str(spec.get("repo") or "")).expanduser().resolve()
    if not repo.is_dir() or not (repo / ".git").exists():
        raise ValueError("repo must be an existing Git repository")
    if not str(spec.get("name") or "").strip():
        raise ValueError("name is required")
    goal = spec.get("goal")
    if not isinstance(goal, dict) or not str(goal.get("goal") or "").strip():
        raise ValueError("goal must be an object containing goal")
    task = spec.get("task") or {}
    if not isinstance(task, dict):
        raise ValueError("task must be an object")
    if not str(task.get("id") or "").strip() and not str(spec.get("project_id") or "").strip():
        raise ValueError("project_id is required when no existing task.id is supplied")
    gates = spec.get("program_gates") or []
    if not isinstance(gates, list):
        raise ValueError("program_gates must be an array")
    for gate in gates:
        if not isinstance(gate, dict):
            raise ValueError("each program gate must be an object")
        _validate_gate_command([str(item) for item in gate.get("argv") or []])
    spec["schema_version"] = SPEC_SCHEMA_VERSION
    spec["repo"] = str(repo)
    spec["name"] = str(spec["name"]).strip()
    spec["project_id"] = str(spec.get("project_id") or "").strip()
    return spec


def _validate_gate_command(argv: Sequence[str]) -> None:
    if not argv or not all(str(item) for item in argv):
        raise ValueError("program gate argv must be a non-empty string array")
    program = Path(str(argv[0])).name.lower()
    if program in FORBIDDEN_GATE_PROGRAMS:
        raise ValueError("unsafe program gate executable was rejected: %s" % program)
    if program in {"sh", "bash", "zsh", "fish"} and "-c" in argv:
        raise ValueError("program gates may not use a shell command string")
    if program.startswith(("python", "pypy")) and "-c" in argv:
        raise ValueError("program gates may not use inline Python code")
    if program in {"node", "ruby", "perl", "pwsh", "powershell"} and any(
        item in argv for item in ("-e", "-c", "-command", "--eval")
    ):
        raise ValueError("program gates may not use inline interpreter code")
    if program == "git" and len(argv) > 1 and argv[1] not in {"diff", "status"}:
        raise ValueError("program gates may only use read-only git diff/status")
    joined = " ".join(argv).lower()
    if any(token in joined for token in (" publish", " deploy", " reset --hard", " clean -f")):
        raise ValueError("external or destructive program gate was rejected")


def _verification_prompt(contract: Mapping[str, Any], lane: Mapping[str, Any], round_index: int) -> str:
    return """INDEPENDENT READ-ONLY FALSIFICATION
Round: %d
Lane: %s
Dimensions: %s
Frozen contract hash: %s

Inspect the repository, current diff, relevant rules and deterministic evidence. Try to
disprove the delivery. You are strictly read-only: do not edit files, configuration,
messages, tasks or evidence. Report only concrete findings with confidence >= 80.
High/critical findings require reproduction or specific evidence.

Return exactly this envelope, with no markdown outside it:
<governance-findings>{"lane_id":"%s","status":"passed|findings|blocked","findings":[{"dimension":"requirements","severity":"high","confidence":90,"summary":"...","location":"path:line","evidence":["..."],"reproducible":true,"introduced_by_change":true}],"error":""}</governance-findings>

Allowed dimensions for this lane: %s
Acceptance criteria: %s
Non-goals: %s
Prohibitions: %s
""" % (
        round_index,
        lane["id"],
        ", ".join(str(item) for item in lane.get("dimensions") or []),
        contract["contract_hash"],
        lane["id"],
        ", ".join(str(item) for item in lane.get("dimensions") or []),
        json.dumps(contract.get("acceptance_criteria") or [], ensure_ascii=False),
        json.dumps(contract.get("non_goals") or [], ensure_ascii=False),
        json.dumps(contract.get("prohibited_behaviors") or [], ensure_ascii=False),
    )


def _repair_prompt(contract: Mapping[str, Any], aggregate: Mapping[str, Any]) -> str:
    brief = {
        "deterministic_failures": aggregate.get("deterministic_failures") or [],
        "blocking_findings": aggregate.get("blocking_findings") or [],
    }
    return """ONE CONSOLIDATED REPAIR ROUND
Frozen contract hash: %s

This is the only automatic repair round. Keep the original goal, scope, prohibitions and
style rules frozen. Reproduce each item, fix only evidence-backed causes, add or improve
regression checks where appropriate, and run focused checks. Do not push, merge, deploy,
publish, buy usage or perform external/irreversible actions.

Repair brief:
%s
""" % (contract["contract_hash"], json.dumps(brief, ensure_ascii=False, indent=2))


def _first_json_object(text: str) -> Optional[Dict[str, Any]]:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _redact_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"(?i)(api[_-]?key|authorization|bearer|oauth[_-]?token|access[_-]?token)(\s*[:=]\s*)([^\s,;]+)",
        lambda match: match.group(1) + match.group(2) + "[REDACTED]",
        text,
    )
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED_TOKEN]", text)
    return text


def _worktree_fingerprint(repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=str(repo),
            env=sanitized_environment(),
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if result.returncode != 0:
        return "unavailable"
    return hashlib.sha256(result.stdout).hexdigest()


def _redact_argv(argv: Sequence[str]) -> List[str]:
    result: List[str] = []
    hide_next = False
    for value in argv:
        text = str(value)
        if hide_next:
            result.append("[REDACTED]")
            hide_next = False
            continue
        lowered = text.lower()
        if lowered in {"--token", "--api-key", "--authorization", "--password"}:
            result.append(text)
            hide_next = True
            continue
        result.append(_redact_text(text))
    return result


def _safe_event_details(value: Mapping[str, Any]) -> Dict[str, Any]:
    allowed = {"attempt", "decision", "model", "passed", "repair_rounds_used"}
    return {key: value[key] for key in allowed if key in value}


def _one_line(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
