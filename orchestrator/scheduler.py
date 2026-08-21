from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .database import Database, utc_now
from .git_service import GitError, GitService
from .models import PreflightResult, TaskRole, TaskStatus
from .prompts import build_prompt
from .quota import SchedulingDecision


class QuotaDeferred(RuntimeError):
    def __init__(self, decision: SchedulingDecision):
        super().__init__("%s 额度暂不可用；%s" % (decision.executor, decision.reason))
        self.decision = decision


class TaskScheduler:
    def __init__(
        self,
        db: Database,
        git: GitService,
        agent: Any,
        max_workers: int = 2,
        cross_review: bool = True,
    ):
        self.db = db
        self.git = git
        self.agent = agent
        self.cross_review = cross_review
        self.max_workers = max(1, max_workers)
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._shutdown = threading.Event()
        self._threads: List[threading.Thread] = []
        self._decisions: Dict[str, SchedulingDecision] = {}
        self._decision_lock = threading.Lock()
        self._quota_deferred_until: Dict[str, float] = {}
        self._preflight = PreflightResult(False, "", "", ["Scheduler not started"])

    def start(self) -> None:
        self.db.recover_interrupted()
        self._preflight = self.agent.preflight()
        for index in range(self.max_workers):
            thread = threading.Thread(
                target=self._worker_loop,
                name="codex-worker-%d" % (index + 1),
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        monitor = threading.Thread(
            target=self._monitor_loop, name="task-monitor", daemon=True
        )
        monitor.start()
        self._threads.append(monitor)

    def stop(self) -> None:
        self._shutdown.set()
        for _ in range(self.max_workers):
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=3)

    def health(self) -> Dict[str, Any]:
        health = {
            "ready": self._preflight.ok,
            "codex_version": self._preflight.version,
            "auth_status": self._preflight.auth_status,
            "problems": self._preflight.problems,
            "workers": self.max_workers,
            "queued": self._queue.qsize(),
        }
        health.update(self.agent.health())
        return health

    @property
    def executor_names(self) -> List[str]:
        return self.agent.names

    def executor_for(self, task_id: str) -> str:
        task = self.db.get_task(task_id)
        if not task:
            raise RuntimeError("Task not found")
        project = self.db.get_project(task["project_id"])
        return self.agent.select(task, project).executor

    def submit(self, task_id: str) -> bool:
        if not self._preflight.ok:
            raise RuntimeError("; ".join(self._preflight.problems))
        task = self.db.get_task(task_id)
        if not task:
            raise RuntimeError("Task not found")
        if task["status"] not in (
            TaskStatus.BACKLOG.value,
            TaskStatus.READY.value,
        ):
            raise RuntimeError(
                "任务当前处于 %s，只有待处理或可执行状态可以启动"
                % task["status"]
            )
        project = self.db.get_project(task["project_id"])
        decision = self.agent.select(task, project)
        if decision.blocked:
            raise QuotaDeferred(decision)
        check = self.agent.check(decision.executor)
        if not check.ok:
            raise RuntimeError(
                "Executor %s is unavailable: %s"
                % (decision.executor, "; ".join(check.problems))
            )
        if self.db.queue_task(task_id):
            self.db.update_task(
                task_id,
                assigned_executor=decision.executor,
                assigned_model=decision.model,
            )
            with self._decision_lock:
                self._decisions[task_id] = decision
            self.db.add_event(task_id, None, "task.scheduled", decision.to_dict())
            self.db.add_event(task_id, None, "task.queued", {})
            self._queue.put(task_id)
            return True
        return False

    def _monitor_loop(self) -> None:
        while not self._shutdown.wait(1.5):
            try:
                self.db.refresh_unblocked_tasks()
                if not self._preflight.ok:
                    continue
                for task_id in self.db.list_auto_startable():
                    if self._quota_deferred_until.get(task_id, 0) > time.time():
                        continue
                    try:
                        self.submit(task_id)
                    except QuotaDeferred as exc:
                        reset_at = exc.decision.defer_until or int(time.time()) + 300
                        self._quota_deferred_until[task_id] = min(
                            float(reset_at + 5), time.time() + 300
                        )
                        self.db.add_event(
                            task_id,
                            None,
                            "task.quota_deferred",
                            exc.decision.to_dict(),
                        )
                    except RuntimeError as exc:
                        # One task whose executor is missing must not silently
                        # stop auto-scheduling for every other task.
                        message = str(exc)
                        self.db.update_task(
                            task_id,
                            status=TaskStatus.FAILED.value,
                            queued=False,
                            error=message,
                        )
                        self.db.add_event(
                            task_id, None, "task.executor_unavailable", {"error": message}
                        )
            except Exception:
                # A monitor failure must not stop already running tasks.
                time.sleep(1)

    def _worker_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                task_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if task_id is None:
                self._queue.task_done()
                return
            try:
                if self.db.claim_task(task_id):
                    self._execute(task_id)
            finally:
                self._queue.task_done()

    def _execute(self, task_id: str) -> None:
        run: Optional[Dict[str, Any]] = None
        try:
            task = self.db.get_task(task_id)
            if not task:
                return
            project = self.db.get_project(task["project_id"])
            if not project:
                raise RuntimeError("Project not found")

            worktree = self._prepare_task_worktree(task, project)
            task = self.db.get_task(task_id) or task
            parent = self.db.get_task(task["parent_id"]) if task.get("parent_id") else None
            messages = self.db.list_messages(task_id)
            prompt = build_prompt(task, project, messages, parent)
            self.db.mark_messages_delivered(task_id)

            with self._decision_lock:
                decision = self._decisions.pop(task_id, None)
            if decision is None:
                decision = self.agent.select(task, project)
            executor = decision.executor
            run = self.db.create_run(task_id)
            self.db.add_event(
                task_id,
                run["id"],
                "run.started",
                {
                    "role": task["role"],
                    "executor": executor,
                    "model": decision.model,
                    "model_tier": decision.model_tier,
                    "quota_mode": decision.mode,
                    "quota_reason": decision.reason,
                },
            )

            def on_event(event_type: str, payload: Dict[str, Any]) -> None:
                self.db.add_event(task_id, run["id"], event_type, payload)

            result = self.agent.run(
                run_id=run["id"],
                role=task["role"],
                worktree=worktree,
                prompt=prompt,
                session_id=task.get("session_id"),
                on_event=on_event,
                executor=executor,
                model=decision.model,
            )
            self.db.set_run_command(run["id"], result.command)
            self.db.finish_run(
                run["id"],
                result.status,
                result.exit_code,
                json.dumps(result.final, ensure_ascii=False),
                result.stderr_tail,
                result.usage,
            )
            if result.session_id:
                self.db.update_task(task_id, session_id=result.session_id)
            if result.status != "complete":
                raise RuntimeError(
                    result.stderr_tail.strip()
                    or "Codex exited with status %s" % result.exit_code
                )

            snapshot = self.git.snapshot(str(worktree))
            if snapshot["status"]:
                self.db.add_event(
                    task_id,
                    run["id"],
                    "git.changes",
                    snapshot,
                )
            self._record_evidence(task_id, result.final)
            self._reject_reviewer_edits(task, result.final, snapshot, run["id"])
            commit = self.git.commit_changes(
                str(worktree), task_id, task["title"]
            )
            if commit:
                self.db.add_event(
                    task_id, run["id"], "git.committed", {"commit": commit}
                )
            self._apply_result(task, result.final, run["id"])
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            if run:
                current_runs = self.db.list_runs(task_id)
                current = next((item for item in current_runs if item["id"] == run["id"]), None)
                if current and current["status"] == "running":
                    self.db.finish_run(run["id"], "failed", 1, "", message, {})
            self.db.update_task(
                task_id,
                status=TaskStatus.FAILED.value,
                queued=False,
                error=message,
            )
            self.db.add_event(task_id, run["id"] if run else None, "run.failed", {"error": message})

    def _prepare_task_worktree(
        self, task: Dict[str, Any], project: Dict[str, Any]
    ) -> Path:
        if task.get("worktree_path"):
            result = self.git.prepare_worktree(
                project["id"],
                task["id"],
                task["title"],
                project["repo_path"],
                project["base_branch"],
                task["worktree_path"],
                task.get("branch_name"),
            )
            return Path(result["worktree_path"])

        dependency_tasks = []
        for dependency in task.get("dependencies", []):
            full = self.db.get_task(dependency["id"])
            if full and full.get("branch_name"):
                dependency_tasks.append(full)
        base_ref = (
            dependency_tasks[0]["branch_name"]
            if dependency_tasks
            else project["base_branch"]
        )
        result = self.git.prepare_worktree(
            project["id"],
            task["id"],
            task["title"],
            project["repo_path"],
            base_ref,
        )
        self.db.update_task(
            task["id"],
            worktree_path=result["worktree_path"],
            branch_name=result["branch_name"],
        )
        remaining = [item["branch_name"] for item in dependency_tasks[1:]]
        if remaining:
            merged = self.git.integrate_branches(result["worktree_path"], remaining)
            self.db.add_event(task["id"], None, "git.dependencies_merged", {"branches": merged})
        self.db.add_event(
            task["id"],
            None,
            "git.worktree_created",
            result,
        )
        return Path(result["worktree_path"])

    def _record_evidence(self, task_id: str, final: Dict[str, Any]) -> None:
        """Persist the agent's claimed checks so a human sees them at the gate.

        These are claims, not proof. Storing them is what makes them auditable:
        an empty list on work that asks for review is itself the signal.
        """
        tests = final.get("tests")
        entries = [str(item).strip() for item in tests if str(item).strip()] if isinstance(tests, list) else []
        self.db.update_task(task_id, evidence=json.dumps(entries, ensure_ascii=False))

    def _reject_reviewer_edits(
        self,
        task: Dict[str, Any],
        final: Dict[str, Any],
        snapshot: Dict[str, Any],
        run_id: str,
    ) -> None:
        """An independent reviewer reports; it does not quietly fix.

        Letting a reviewer's edits ride along on the branch destroys the point of
        the role twice over: the finding never gets stated, and the human reading
        the diff can no longer tell implementation from review.
        """
        if task["role"] != TaskRole.REVIEWER.value:
            return
        changed = self.git.worktree_modifications(snapshot.get("status", ""))
        if not changed:
            return
        # Keep the report readable even though the run is about to fail.
        self.db.update_task(
            task["id"],
            summary=str(final.get("summary", "")).strip(),
            handoff=str(final.get("handoff_notes", "")).strip(),
        )
        self.db.add_event(
            task["id"], run_id, "review.contract_violation", {"files": changed[:50]}
        )
        raise RuntimeError(
            "评审者修改或新增了文件，违反独立评审契约，改动未提交：%s"
            % "、".join(changed[:10])
        )

    def _apply_result(
        self, task: Dict[str, Any], final: Dict[str, Any], run_id: str
    ) -> None:
        summary = str(final.get("summary", "")).strip()
        handoff = str(final.get("handoff_notes", "")).strip()
        self.db.update_task(task["id"], summary=summary, handoff=handoff, error="")
        self._apply_delegation(task, final.get("proposed_tasks") or [])
        self._apply_messages(task, final.get("messages") or [])

        outcome = final.get("outcome")
        recommended = final.get("recommended_stage")
        question = str(final.get("approval_question", "")).strip()
        if outcome == "failed":
            raise RuntimeError(summary or "Agent reported failure")
        if outcome == "blocked" or recommended == "blocked":
            reason = summary or "Agent reported an external blocker without details"
            self.db.update_task(
                task["id"], status=TaskStatus.BLOCKED.value, error=reason
            )
            self.db.add_event(task["id"], run_id, "task.blocked", {"reason": reason})
            return
        if outcome == "needs_approval" or recommended == "waiting_approval":
            question = question or "请确认下一步如何处理此任务。"
            self.db.create_approval(task["id"], question, kind="resume")
            self.db.update_task(
                task["id"],
                status=TaskStatus.WAITING_APPROVAL.value,
                approval_question=question,
            )
            self.db.add_event(task["id"], run_id, "approval.requested", {"question": question})
            return
        if task["requires_approval"]:
            question = question or "请审核该任务的交付结果；批准后任务完成并释放下游任务。"
            self.db.create_approval(task["id"], question, kind="complete")
            self.db.update_task(
                task["id"],
                status=TaskStatus.WAITING_APPROVAL.value,
                approval_question=question,
            )
            self.db.add_event(task["id"], run_id, "approval.requested", {"question": question})
            return
        if recommended == "review":
            self.db.update_task(task["id"], status=TaskStatus.REVIEW.value)
            self.db.add_event(task["id"], run_id, "task.ready_for_review", {})
            return
        self._mark_done(task["id"], run_id)

    def _apply_delegation(
        self, parent: Dict[str, Any], proposals: List[Dict[str, Any]]
    ) -> None:
        if not parent.get("allow_delegation") or not proposals:
            return
        proposals = proposals[:20]
        created: Dict[str, Dict[str, Any]] = {}
        for proposal in proposals:
            title = str(proposal.get("title", "")).strip()[:160]
            if not title or title in created:
                continue
            role = str(proposal.get("role", "implementer"))
            child = self.db.create_task(
                project_id=parent["project_id"],
                parent_id=parent["id"],
                title=title,
                description=str(proposal.get("description", ""))[:8000],
                role=role,
                executor=self._child_executor(parent, role),
                status=TaskStatus.BLOCKED.value,
                requires_approval=bool(proposal.get("requires_approval")),
                allow_delegation=False,
                auto_start=bool(proposal.get("auto_start")),
                dependencies=[parent["id"]],
            )
            created[title] = child
        for proposal in proposals:
            child = created.get(str(proposal.get("title", "")).strip()[:160])
            if not child:
                continue
            for dependency_title in proposal.get("depends_on_titles") or []:
                dependency = created.get(str(dependency_title).strip()[:160])
                if dependency and dependency["id"] != child["id"]:
                    self.db.add_dependency(child["id"], dependency["id"])
        self.db.add_event(
            parent["id"],
            None,
            "tasks.delegated",
            {"children": [{"id": item["id"], "title": title} for title, item in created.items()]},
        )

    def _child_executor(self, parent: Dict[str, Any], role: str) -> str:
        """Children inherit the parent's executor, except independent reviewers.

        A model reviewing its own output is the weakest link in the whole gate
        chain, so a reviewer is handed to a different CLI whenever one is ready.
        """
        inherited = str(parent.get("executor", ""))
        if role != TaskRole.REVIEWER.value or not self.cross_review:
            return inherited
        current = self.agent.resolve_name(
            {"executor": inherited}, self.db.get_project(parent["project_id"])
        )
        return self.agent.alternate_name(current) or inherited

    def _apply_messages(
        self, sender_task: Dict[str, Any], messages: List[Dict[str, Any]]
    ) -> None:
        for message in messages[:30]:
            recipient_id = str(message.get("recipient_task_id", ""))
            body = str(message.get("body", "")).strip()
            recipient = self.db.get_task(recipient_id)
            if (
                not recipient
                or recipient["project_id"] != sender_task["project_id"]
                or not body
            ):
                continue
            self.db.add_message(
                recipient_id,
                sender="Agent %s" % sender_task["id"],
                sender_task_id=sender_task["id"],
                body=body[:10000],
                kind="agent_handoff",
            )

    def _mark_done(self, task_id: str, run_id: Optional[str] = None) -> None:
        self.db.update_task(
            task_id,
            status=TaskStatus.DONE.value,
            completed_at=utc_now(),
            approval_question="",
            queued=False,
        )
        self.db.add_event(task_id, run_id, "task.completed", {})
        task = self.db.get_task(task_id)
        if task:
            self.db.refresh_unblocked_tasks(task["project_id"])

    def resolve_approval(self, task_id: str, approved: bool, note: str) -> Dict[str, Any]:
        task = self.db.get_task(task_id)
        if not task or task["status"] != TaskStatus.WAITING_APPROVAL.value:
            raise ValueError("Task is not waiting for approval")
        approval = self.db.resolve_approval(task_id, approved, note)
        decision = "批准" if approved else "拒绝"
        message = "人工审批结果：%s。%s" % (decision, note.strip() or "未提供补充说明。")
        self.db.add_message(task_id, "Human approver", message, kind="approval")
        self.db.add_event(
            task_id,
            None,
            "approval.resolved",
            {"approved": approved, "note": note, "kind": approval["kind"]},
        )
        if approved and approval["kind"] == "complete":
            self._mark_done(task_id)
        else:
            self.db.update_task(
                task_id,
                status=TaskStatus.READY.value,
                approval_question="",
                queued=False,
            )
            self.submit(task_id)
        return approval

    def review_decision(self, task_id: str, accepted: bool, note: str) -> None:
        task = self.db.get_task(task_id)
        if not task or task["status"] != TaskStatus.REVIEW.value:
            raise ValueError("Task is not in review")
        if accepted:
            self.db.add_message(
                task_id, "Human reviewer", note or "评审通过。", kind="review"
            )
            self._mark_done(task_id)
            return
        self.db.add_message(
            task_id,
            "Human reviewer",
            "评审要求修改：%s" % (note.strip() or "请重新检查交付结果。"),
            kind="review",
        )
        self.db.update_task(task_id, status=TaskStatus.READY.value, queued=False)
        self.submit(task_id)
