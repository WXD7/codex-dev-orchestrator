from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from .models import TaskStatus, WRITE_ROLES, normalize_required_artifacts


ALERT_EVENT_SEVERITY = {
    "run.failed": "error",
    "task.executor_unavailable": "error",
    "review.contract_violation": "error",
    "artifact.validation_failed": "error",
    "task.blocked": "warning",
    "task.quota_deferred": "warning",
    "codex.stderr": "warning",
    "claude.stderr": "warning",
}

# These messages are emitted by the local Codex installation while loading a
# plugin icon. They do not describe the task or its sandbox, and four identical
# warnings otherwise hide the one runtime violation an operator actually needs.
IGNORED_ALERT_FRAGMENTS = (
    "codex_skills::interface: ignoring interface.icon_",
)

LOG_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+\s+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    repo_path TEXT NOT NULL,
                    base_branch TEXT NOT NULL DEFAULT 'HEAD',
                    workflow TEXT NOT NULL DEFAULT 'feature-dev',
                    default_executor TEXT NOT NULL DEFAULT '',
                    auto_start INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    parent_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'implementer',
                    executor TEXT NOT NULL DEFAULT '',
                    assigned_executor TEXT NOT NULL DEFAULT '',
                    assigned_model TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'backlog',
                    priority INTEGER NOT NULL DEFAULT 50,
                    branch_name TEXT,
                    worktree_path TEXT,
                    session_id TEXT,
                    summary TEXT NOT NULL DEFAULT '',
                    handoff TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '',
                    required_artifacts TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL DEFAULT '',
                    approval_question TEXT NOT NULL DEFAULT '',
                    requires_approval INTEGER NOT NULL DEFAULT 0,
                    allow_delegation INTEGER NOT NULL DEFAULT 0,
                    auto_start INTEGER NOT NULL DEFAULT 0,
                    queued INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_project_status
                    ON tasks(project_id, status, priority, created_at);
                CREATE INDEX IF NOT EXISTS idx_tasks_parent
                    ON tasks(parent_id);

                CREATE TABLE IF NOT EXISTS task_dependencies (
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    depends_on TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    PRIMARY KEY(task_id, depends_on),
                    CHECK(task_id <> depends_on)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    sender_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                    sender TEXT NOT NULL,
                    body TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'message',
                    created_at TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_messages_task
                    ON messages(task_id, id);

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    command_json TEXT NOT NULL DEFAULT '[]',
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    exit_code INTEGER,
                    final_output TEXT NOT NULL DEFAULT '',
                    stderr_tail TEXT NOT NULL DEFAULT '',
                    usage_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_runs_task
                    ON runs(task_id, started_at);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_task
                    ON events(task_id, id);

                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    kind TEXT NOT NULL DEFAULT 'resume',
                    question TEXT NOT NULL,
                    decision_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_approvals_task
                    ON approvals(task_id, created_at);
                """
            )
            approval_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(approvals)").fetchall()
            }
            if "kind" not in approval_columns:
                conn.execute(
                    "ALTER TABLE approvals ADD COLUMN kind TEXT NOT NULL DEFAULT 'resume'"
                )
            for table, column in (
                ("tasks", "executor"),
                ("tasks", "assigned_executor"),
                ("tasks", "assigned_model"),
                ("tasks", "evidence"),
                ("tasks", "required_artifacts"),
                ("projects", "default_executor"),
            ):
                existing = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(%s)" % table).fetchall()
                }
                if column not in existing:
                    default = "'[]'" if column == "required_artifacts" else "''"
                    conn.execute(
                        "ALTER TABLE %s ADD COLUMN %s TEXT NOT NULL DEFAULT %s"
                        % (table, column, default)
                    )

    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        item = dict(row)
        for key in (
            "auto_start",
            "requires_approval",
            "allow_delegation",
            "queued",
        ):
            if key in item:
                item[key] = bool(item[key])
        if "required_artifacts" in item:
            try:
                item["required_artifacts"] = normalize_required_artifacts(
                    json.loads(item["required_artifacts"] or "[]")
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("Stored required_artifacts are invalid") from exc
        return item

    def create_project(
        self,
        name: str,
        repo_path: str,
        base_branch: str = "HEAD",
        workflow: str = "feature-dev",
        auto_start: bool = False,
        default_executor: str = "",
    ) -> Dict[str, Any]:
        project_id = new_id("prj")
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO projects
                    (id, name, repo_path, base_branch, workflow, default_executor,
                     auto_start, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    name.strip(),
                    repo_path,
                    base_branch or "HEAD",
                    workflow,
                    default_executor.strip(),
                    int(auto_start),
                    now,
                    now,
                ),
            )
        return self.get_project(project_id)  # type: ignore[return-value]

    def list_projects(self) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT p.*,
                       COUNT(t.id) AS task_count,
                       SUM(CASE WHEN t.status = 'running' THEN 1 ELSE 0 END) AS running_count
                FROM projects p
                LEFT JOIN tasks t ON t.project_id = p.id
                GROUP BY p.id
                ORDER BY p.created_at DESC
                """
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return self._row(row)

    def create_task(
        self,
        project_id: str,
        title: str,
        description: str = "",
        role: str = "implementer",
        executor: str = "",
        parent_id: Optional[str] = None,
        status: str = TaskStatus.BACKLOG.value,
        priority: int = 50,
        requires_approval: bool = False,
        allow_delegation: bool = False,
        auto_start: bool = False,
        dependencies: Sequence[str] = (),
        required_artifacts: Sequence[str] = (),
    ) -> Dict[str, Any]:
        task_id = new_id("tsk")
        now = utc_now()
        artifacts = normalize_required_artifacts(required_artifacts)
        if artifacts and role not in WRITE_ROLES:
            raise ValueError(
                "Only implementer or qa tasks may require file artifacts"
            )
        if not self.get_project(project_id):
            raise ValueError("Project not found")
        if parent_id:
            parent = self.get_task(parent_id)
            if not parent or parent["project_id"] != project_id:
                raise ValueError("Parent task must belong to the same project")
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, project_id, parent_id, title, description, role, executor,
                    status, priority, requires_approval, allow_delegation, auto_start,
                    required_artifacts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    project_id,
                    parent_id,
                    title.strip(),
                    description.strip(),
                    role,
                    executor.strip(),
                    status,
                    max(0, min(100, int(priority))),
                    int(requires_approval),
                    int(allow_delegation),
                    int(auto_start),
                    json.dumps(artifacts, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            for dependency in dependencies:
                self._validate_dependency(conn, task_id, dependency, project_id)
                conn.execute(
                    "INSERT OR IGNORE INTO task_dependencies(task_id, depends_on) VALUES (?, ?)",
                    (task_id, dependency),
                )
            if dependencies:
                incomplete = conn.execute(
                    """
                    SELECT 1 FROM task_dependencies d
                    JOIN tasks t ON t.id = d.depends_on
                    WHERE d.task_id = ? AND t.status <> 'done' LIMIT 1
                    """,
                    (task_id,),
                ).fetchone()
                dependency_status = (
                    TaskStatus.BLOCKED.value
                    if incomplete is not None
                    else TaskStatus.READY.value
                )
                conn.execute(
                    "UPDATE tasks SET status = ? WHERE id = ?",
                    (dependency_status, task_id),
                )
        self.add_event(
            task_id,
            None,
            "task.created",
            {
                "title": title,
                "role": role,
                "executor": executor.strip(),
                "required_artifacts": artifacts,
            },
        )
        return self.get_task(task_id)  # type: ignore[return-value]

    def _validate_dependency(
        self,
        conn: sqlite3.Connection,
        task_id: str,
        depends_on: str,
        project_id: str,
    ) -> None:
        if task_id == depends_on:
            raise ValueError("A task cannot depend on itself")
        row = conn.execute(
            "SELECT project_id FROM tasks WHERE id = ?", (depends_on,)
        ).fetchone()
        if not row or row["project_id"] != project_id:
            raise ValueError("Dependency must belong to the same project")
        if self._dependency_reaches(conn, depends_on, task_id):
            raise ValueError("Dependency would create a cycle")

    def _dependency_reaches(
        self, conn: sqlite3.Connection, start: str, target: str
    ) -> bool:
        row = conn.execute(
            """
            WITH RECURSIVE chain(task_id) AS (
                SELECT depends_on FROM task_dependencies WHERE task_id = ?
                UNION
                SELECT d.depends_on
                FROM task_dependencies d
                JOIN chain c ON d.task_id = c.task_id
            )
            SELECT 1 FROM chain WHERE task_id = ? LIMIT 1
            """,
            (start, target),
        ).fetchone()
        return row is not None

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        task = self.get_task(task_id)
        if not task:
            raise ValueError("Task not found")
        with self.connection() as conn:
            self._validate_dependency(conn, task_id, depends_on, task["project_id"])
            conn.execute(
                "INSERT OR IGNORE INTO task_dependencies(task_id, depends_on) VALUES (?, ?)",
                (task_id, depends_on),
            )
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ? AND status NOT IN ('done', 'running')",
                (TaskStatus.BLOCKED.value, utc_now(), task_id),
            )

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            item = self._row(row) or {}
            deps = conn.execute(
                """
                SELECT t.id, t.title, t.status
                FROM task_dependencies d
                JOIN tasks t ON t.id = d.depends_on
                WHERE d.task_id = ?
                ORDER BY t.created_at
                """,
                (task_id,),
            ).fetchall()
            children = conn.execute(
                "SELECT id, title, role, status, summary FROM tasks WHERE parent_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchall()
            item["dependencies"] = [dict(dep) for dep in deps]
            item["children"] = [dict(child) for child in children]
            return item

    def list_tasks(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT t.*,
                       (SELECT COUNT(*) FROM task_dependencies d WHERE d.task_id = t.id) AS dependency_count,
                       (SELECT COUNT(*) FROM tasks c WHERE c.parent_id = t.id) AS child_count
                FROM tasks t
                WHERE t.project_id = ?
                ORDER BY t.priority DESC, t.created_at ASC
                """,
                (project_id,),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def update_task(self, task_id: str, **fields: Any) -> Dict[str, Any]:
        allowed = {
            "status",
            "executor",
            "assigned_executor",
            "assigned_model",
            "branch_name",
            "worktree_path",
            "session_id",
            "summary",
            "handoff",
            "evidence",
            "required_artifacts",
            "error",
            "approval_question",
            "queued",
            "started_at",
            "completed_at",
            "auto_start",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            task = self.get_task(task_id)
            if not task:
                raise ValueError("Task not found")
            return task
        updates["updated_at"] = utc_now()
        if "required_artifacts" in updates:
            task = self.get_task(task_id)
            if not task:
                raise ValueError("Task not found")
            artifacts = normalize_required_artifacts(updates["required_artifacts"])
            if artifacts and task["role"] not in WRITE_ROLES:
                raise ValueError(
                    "Only implementer or qa tasks may require file artifacts"
                )
            updates["required_artifacts"] = json.dumps(
                artifacts, ensure_ascii=False
            )
        bool_fields = {"queued", "auto_start"}
        for key in bool_fields:
            if key in updates:
                updates[key] = int(bool(updates[key]))
        assignments = ", ".join("%s = ?" % key for key in updates)
        values = list(updates.values()) + [task_id]
        with self.connection() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET %s WHERE id = ?" % assignments, values
            )
            if cursor.rowcount != 1:
                raise ValueError("Task not found")
        return self.get_task(task_id)  # type: ignore[return-value]

    def dependencies_complete(self, task_id: str) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS incomplete
                FROM task_dependencies d
                JOIN tasks t ON t.id = d.depends_on
                WHERE d.task_id = ? AND t.status <> 'done'
                """,
                (task_id,),
            ).fetchone()
        return bool(row and row["incomplete"] == 0)

    def refresh_unblocked_tasks(self, project_id: Optional[str] = None) -> List[str]:
        # An empty error marks dependency blocking. Agent-reported external
        # blockers carry a reason in error and must remain blocked until a
        # human explicitly retries them.
        sql = "SELECT id FROM tasks WHERE status = 'blocked' AND error = ''"
        params: List[Any] = []
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        changed: List[str] = []
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            for row in rows:
                task_id = row["id"]
                incomplete = conn.execute(
                    """
                    SELECT 1 FROM task_dependencies d
                    JOIN tasks t ON t.id = d.depends_on
                    WHERE d.task_id = ? AND t.status <> 'done' LIMIT 1
                    """,
                    (task_id,),
                ).fetchone()
                if incomplete is None:
                    conn.execute(
                        "UPDATE tasks SET status = 'ready', updated_at = ? WHERE id = ?",
                        (utc_now(), task_id),
                    )
                    changed.append(task_id)
        for task_id in changed:
            self.add_event(task_id, None, "task.unblocked", {})
        return changed

    def claim_task(self, task_id: str) -> bool:
        now = utc_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not row or row["status"] not in (
                TaskStatus.READY.value,
                TaskStatus.BACKLOG.value,
                TaskStatus.FAILED.value,
            ):
                return False
            incomplete = conn.execute(
                """
                SELECT 1 FROM task_dependencies d
                JOIN tasks t ON t.id = d.depends_on
                WHERE d.task_id = ? AND t.status <> 'done' LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if incomplete:
                conn.execute(
                    "UPDATE tasks SET status = 'blocked', queued = 0, updated_at = ? WHERE id = ?",
                    (now, task_id),
                )
                return False
            conn.execute(
                """
                UPDATE tasks
                SET status = 'running', queued = 0, started_at = COALESCE(started_at, ?),
                    error = '', updated_at = ?
                WHERE id = ?
                """,
                (now, now, task_id),
            )
        return True

    def queue_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task:
            raise ValueError("Task not found")
        if task["queued"] or task["status"] == TaskStatus.RUNNING.value:
            return False
        if task["status"] not in (
            TaskStatus.BACKLOG.value,
            TaskStatus.READY.value,
        ):
            return False
        if not self.dependencies_complete(task_id):
            self.update_task(task_id, status=TaskStatus.BLOCKED.value, queued=False)
            return False
        with self.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = ?, queued = 1, updated_at = ?
                WHERE id = ? AND queued = 0 AND status IN (?, ?)
                """,
                (
                    TaskStatus.READY.value,
                    utc_now(),
                    task_id,
                    TaskStatus.BACKLOG.value,
                    TaskStatus.READY.value,
                ),
            )
        return cursor.rowcount == 1

    def list_auto_startable(self) -> List[str]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT t.id
                FROM tasks t JOIN projects p ON p.id = t.project_id
                WHERE t.status = 'ready' AND t.queued = 0
                  AND (t.auto_start = 1 OR p.auto_start = 1)
                ORDER BY t.priority DESC, t.created_at
                """
            ).fetchall()
        return [row["id"] for row in rows]

    def add_message(
        self,
        task_id: str,
        sender: str,
        body: str,
        sender_task_id: Optional[str] = None,
        kind: str = "message",
    ) -> Dict[str, Any]:
        now = utc_now()
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO messages(task_id, sender_task_id, sender, body, kind, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, sender_task_id, sender, body.strip(), kind, now),
            )
            message_id = cursor.lastrowid
        self.add_event(task_id, None, "message.created", {"message_id": message_id, "sender": sender})
        return {
            "id": message_id,
            "task_id": task_id,
            "sender_task_id": sender_task_id,
            "sender": sender,
            "body": body.strip(),
            "kind": kind,
            "created_at": now,
            "delivered": False,
        }

    def list_messages(self, task_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE task_id = ? ORDER BY id DESC LIMIT ?",
                (task_id, max(1, min(500, limit))),
            ).fetchall()
        result = [dict(row) for row in reversed(rows)]
        for item in result:
            item["delivered"] = bool(item["delivered"])
        return result

    def mark_messages_delivered(self, task_id: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE messages SET delivered = 1 WHERE task_id = ?", (task_id,)
            )

    def create_run(self, task_id: str) -> Dict[str, Any]:
        run_id = new_id("run")
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO runs(id, task_id, status, started_at) VALUES (?, ?, 'running', ?)",
                (run_id, task_id, now),
            )
        return {"id": run_id, "task_id": task_id, "status": "running", "started_at": now}

    def set_run_command(self, run_id: str, command: Sequence[str]) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE runs SET command_json = ? WHERE id = ?",
                (json.dumps(list(command), ensure_ascii=False), run_id),
            )

    def finish_run(
        self,
        run_id: str,
        status: str,
        exit_code: int,
        final_output: str,
        stderr_tail: str,
        usage: Dict[str, Any],
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?, completed_at = ?, exit_code = ?, final_output = ?,
                    stderr_tail = ?, usage_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    utc_now(),
                    exit_code,
                    final_output,
                    stderr_tail[-12000:],
                    json.dumps(usage, ensure_ascii=False),
                    run_id,
                ),
            )

    def list_runs(self, task_id: str) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE task_id = ? ORDER BY started_at DESC",
                (task_id,),
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["command"] = json.loads(item.pop("command_json") or "[]")
            item["usage"] = json.loads(item.pop("usage_json") or "{}")
            result.append(item)
        return result

    def add_event(
        self,
        task_id: str,
        run_id: Optional[str],
        event_type: str,
        payload: Dict[str, Any],
    ) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events(task_id, run_id, type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    run_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_events(self, task_id: str, after: int = 0, limit: int = 300) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE task_id = ? AND id > ?
                ORDER BY id ASC LIMIT ?
                """,
                (task_id, max(0, after), max(1, min(1000, limit))),
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result

    def list_alert_events(self, task_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Return operator-facing failures and warnings, newest first.

        The event table remains the lossless audit log. This view is deliberately
        smaller: known installation noise is removed and repeated lines are
        grouped so a noisy CLI cannot bury the actionable signal.
        """
        bounded_limit = max(1, min(100, int(limit)))
        event_types = tuple(ALERT_EVENT_SEVERITY)
        placeholders = ",".join("?" for _ in event_types)
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE task_id = ? AND type IN (%s)
                ORDER BY id DESC LIMIT 3000
                """ % placeholders,
                (task_id, *event_types),
            ).fetchall()

        alerts: List[Dict[str, Any]] = []
        grouped: Dict[Any, Dict[str, Any]] = {}
        for row in rows:
            event = dict(row)
            payload = json.loads(event.pop("payload_json") or "{}")
            if not isinstance(payload, dict):
                payload = {}
            message = self._alert_message(event["type"], payload)
            if not message or any(fragment in message for fragment in IGNORED_ALERT_FRAGMENTS):
                continue
            dedupe_message = LOG_TIMESTAMP.sub("", message).strip()
            key = (event["type"], dedupe_message)
            existing = grouped.get(key)
            if existing:
                existing["occurrences"] += 1
                continue
            severity = ALERT_EVENT_SEVERITY[event["type"]]
            lowered = message.lower()
            if event["type"].endswith(".stderr") and re.search(
                r"\b(error|fatal|panic|violation)\b", lowered
            ):
                severity = "error"
            alert = {
                "id": event["id"],
                "run_id": event["run_id"],
                "type": event["type"],
                "severity": severity,
                "message": LOG_TIMESTAMP.sub("", message).strip()[:1200],
                "occurrences": 1,
                "created_at": event["created_at"],
            }
            grouped[key] = alert
            alerts.append(alert)
            if len(alerts) >= bounded_limit:
                break
        return alerts

    @staticmethod
    def _alert_message(event_type: str, payload: Dict[str, Any]) -> str:
        if event_type == "task.blocked":
            return str(payload.get("reason") or "Agent 报告任务被阻塞")
        if event_type == "task.quota_deferred":
            return str(payload.get("reason") or "订阅额度不足，任务等待额度窗口刷新")
        if event_type == "review.contract_violation":
            files = payload.get("files") or []
            if isinstance(files, list) and files:
                return "独立评审者修改或新增了文件：%s" % "、".join(
                    str(item) for item in files[:10]
                )
            return "独立评审者修改了工作树，违反评审契约"
        if event_type.endswith(".stderr"):
            return str(payload.get("line") or "")
        return str(payload.get("error") or payload.get("reason") or event_type)

    def create_approval(
        self, task_id: str, question: str, kind: str = "resume"
    ) -> Dict[str, Any]:
        if kind not in ("resume", "complete"):
            raise ValueError("Invalid approval kind")
        approval_id = new_id("apr")
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE approvals SET status = 'superseded', resolved_at = ?
                WHERE task_id = ? AND status = 'pending'
                """,
                (now, task_id),
            )
            conn.execute(
                """
                INSERT INTO approvals(id, task_id, status, kind, question, created_at)
                VALUES (?, ?, 'pending', ?, ?, ?)
                """,
                (approval_id, task_id, kind, question, now),
            )
        return {
            "id": approval_id,
            "task_id": task_id,
            "status": "pending",
            "kind": kind,
            "question": question,
            "created_at": now,
        }

    def pending_approval(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM approvals
                WHERE task_id = ? AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        """Every approval currently waiting on a human, across all projects."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT a.id, a.task_id, a.kind, a.question, a.created_at,
                       t.title AS task_title, t.role AS task_role,
                       t.status AS task_status, t.project_id,
                       p.name AS project_name
                FROM approvals a
                JOIN tasks t ON t.id = a.task_id
                JOIN projects p ON p.id = t.project_id
                WHERE a.status = 'pending'
                ORDER BY a.created_at ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_tasks_awaiting_review(self) -> List[Dict[str, Any]]:
        """Tasks parked in human code review, across all projects."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT t.id AS task_id, t.title AS task_title, t.role AS task_role,
                       t.summary, t.branch_name, t.project_id,
                       t.updated_at, p.name AS project_name
                FROM tasks t
                JOIN projects p ON p.id = t.project_id
                WHERE t.status = ?
                ORDER BY t.updated_at ASC
                """,
                (TaskStatus.REVIEW.value,),
            ).fetchall()
        return [dict(row) for row in rows]

    def resolve_approval(self, task_id: str, approved: bool, note: str) -> Dict[str, Any]:
        approval = self.pending_approval(task_id)
        if not approval:
            raise ValueError("No pending approval")
        status = "approved" if approved else "rejected"
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE approvals
                SET status = ?, decision_note = ?, resolved_at = ?
                WHERE id = ?
                """,
                (status, note.strip(), utc_now(), approval["id"]),
            )
        approval.update({"status": status, "decision_note": note.strip()})
        return approval

    def recover_interrupted(self) -> int:
        now = utc_now()
        with self.connection() as conn:
            running = conn.execute(
                "SELECT id FROM tasks WHERE status = 'running'"
            ).fetchall()
            conn.execute(
                """
                UPDATE tasks
                SET status = 'failed', queued = 0,
                    error = 'Server stopped while this task was running', updated_at = ?
                WHERE status = 'running'
                """,
                (now,),
            )
            conn.execute(
                """
                UPDATE runs
                SET status = 'failed', completed_at = ?, exit_code = -1,
                    stderr_tail = 'Server stopped while this run was active'
                WHERE status = 'running'
                """,
                (now,),
            )
            conn.execute("UPDATE tasks SET queued = 0 WHERE queued = 1")
        return len(running)
