from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .database import Database
from .git_service import GitError, GitService
from .models import TaskRole, TaskStatus
from .scheduler import TaskScheduler


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Application:
    def __init__(
        self,
        db: Database,
        git: GitService,
        scheduler: TaskScheduler,
        static_dir: Path,
    ):
        self.db = db
        self.git = git
        self.scheduler = scheduler
        self.static_dir = Path(static_dir).resolve()

    def get(self, path: str, query: Dict[str, Any]) -> Tuple[int, Any]:
        if path == "/api/health":
            return 200, {"service": "ok", "agent": self.scheduler.health()}
        if path == "/api/projects":
            return 200, {"projects": self.db.list_projects()}
        if path.startswith("/api/projects/") and path.endswith("/tasks"):
            project_id = path.split("/")[3]
            self._project(project_id)
            return 200, {"tasks": self.db.list_tasks(project_id)}
        if path.startswith("/api/tasks/"):
            parts = path.strip("/").split("/")
            if len(parts) < 3:
                raise ApiError(404, "Not found")
            task_id = parts[2]
            task = self._task(task_id)
            if len(parts) == 3:
                task["messages"] = self.db.list_messages(task_id)
                task["runs"] = self.db.list_runs(task_id)
                task["approval"] = self.db.pending_approval(task_id)
                return 200, task
            if parts[3] == "events":
                after = int((query.get("after") or ["0"])[0])
                return 200, {"events": self.db.list_events(task_id, after=after)}
            if parts[3] == "changes":
                project = self._project(task["project_id"])
                if not task.get("worktree_path"):
                    return 200, {"status": "", "commits": "", "stat": "", "diff": ""}
                try:
                    return 200, self.git.branch_snapshot(
                        task["worktree_path"], project["base_branch"]
                    )
                except GitError as exc:
                    raise ApiError(409, str(exc)) from exc
        raise ApiError(404, "Not found")

    def post(self, path: str, body: Dict[str, Any]) -> Tuple[int, Any]:
        if path == "/api/projects":
            name = str(body.get("name", "")).strip()
            repo_path = str(body.get("repo_path", "")).strip()
            base_branch = str(body.get("base_branch", "HEAD")).strip() or "HEAD"
            if not name or not repo_path:
                raise ApiError(400, "项目名称和仓库路径不能为空")
            try:
                repo = self.git.validate_repository(repo_path, base_branch)
            except GitError as exc:
                raise ApiError(400, str(exc)) from exc
            project = self.db.create_project(
                name=name,
                repo_path=repo["repo_path"],
                base_branch=base_branch,
                workflow=str(body.get("workflow", "feature-dev")),
                auto_start=bool(body.get("auto_start", False)),
            )
            return 201, project
        if path == "/api/tasks":
            project_id = str(body.get("project_id", ""))
            self._project(project_id)
            title = str(body.get("title", "")).strip()
            if not title:
                raise ApiError(400, "任务标题不能为空")
            role = str(body.get("role", TaskRole.IMPLEMENTER.value))
            if role not in {item.value for item in TaskRole}:
                raise ApiError(400, "无效的 Agent 角色")
            dependencies = body.get("dependencies") or []
            if not isinstance(dependencies, list):
                raise ApiError(400, "dependencies 必须是数组")
            try:
                task = self.db.create_task(
                    project_id=project_id,
                    parent_id=body.get("parent_id") or None,
                    title=title,
                    description=str(body.get("description", "")),
                    role=role,
                    status=TaskStatus.BACKLOG.value,
                    priority=int(body.get("priority", 50)),
                    requires_approval=bool(body.get("requires_approval", False)),
                    allow_delegation=bool(body.get("allow_delegation", False)),
                    auto_start=bool(body.get("auto_start", False)),
                    dependencies=[str(item) for item in dependencies],
                )
            except ValueError as exc:
                raise ApiError(400, str(exc)) from exc
            if body.get("start_now") and task["status"] != TaskStatus.BLOCKED.value:
                self.scheduler.submit(task["id"])
                task = self._task(task["id"])
            return 201, task

        if path.startswith("/api/tasks/"):
            parts = path.strip("/").split("/")
            if len(parts) != 4:
                raise ApiError(404, "Not found")
            task_id, action = parts[2], parts[3]
            task = self._task(task_id)
            if action == "start":
                try:
                    queued = self.scheduler.submit(task_id)
                except RuntimeError as exc:
                    raise ApiError(409, str(exc)) from exc
                return 202, {"queued": queued, "task": self._task(task_id)}
            if action == "message":
                message = str(body.get("body", "")).strip()
                if not message:
                    raise ApiError(400, "消息不能为空")
                created = self.db.add_message(task_id, "Human", message)
                return 201, created
            if action == "approval":
                approved = bool(body.get("approved", False))
                note = str(body.get("note", ""))
                try:
                    approval = self.scheduler.resolve_approval(
                        task_id, approved, note
                    )
                except (ValueError, RuntimeError) as exc:
                    raise ApiError(409, str(exc)) from exc
                return 200, approval
            if action == "review":
                accepted = bool(body.get("accepted", False))
                note = str(body.get("note", ""))
                try:
                    self.scheduler.review_decision(task_id, accepted, note)
                except (ValueError, RuntimeError) as exc:
                    raise ApiError(409, str(exc)) from exc
                return 200, self._task(task_id)
            if action == "retry":
                if task["status"] not in (
                    TaskStatus.FAILED.value,
                    TaskStatus.BLOCKED.value,
                ):
                    raise ApiError(409, "只有失败或阻塞的任务可以重试")
                self.db.update_task(task_id, status=TaskStatus.READY.value, error="", queued=False)
                try:
                    queued = self.scheduler.submit(task_id)
                except RuntimeError as exc:
                    raise ApiError(409, str(exc)) from exc
                return 202, {"queued": queued, "task": self._task(task_id)}
        raise ApiError(404, "Not found")

    def _project(self, project_id: str) -> Dict[str, Any]:
        project = self.db.get_project(project_id)
        if not project:
            raise ApiError(404, "Project not found")
        return project

    def _task(self, task_id: str) -> Dict[str, Any]:
        task = self.db.get_task(task_id)
        if not task:
            raise ApiError(404, "Task not found")
        return task


def make_handler(app: Application):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CodexOrchestrator/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            print("%s - %s" % (self.address_string(), fmt % args))

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self._handle_api("GET", parsed.path, parse_qs(parsed.query))
                return
            self._serve_static(parsed.path)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self._json(404, {"error": "Not found"})
                return
            self._handle_api("POST", parsed.path, self._read_json())

        def _handle_api(self, method: str, path: str, data: Dict[str, Any]) -> None:
            try:
                status, payload = app.get(path, data) if method == "GET" else app.post(path, data)
                self._json(status, payload)
            except ApiError as exc:
                self._json(exc.status, {"error": exc.message})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": "Internal error: %s" % exc})

        def _read_json(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ApiError(413, "Request body too large")
            if length == 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ApiError(400, "JSON body must be an object")
            return value

        def _json(self, status: int, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _serve_static(self, path: str) -> None:
            relative = "index.html" if path in ("", "/") else path.lstrip("/")
            if relative not in {"index.html", "app.js", "styles.css"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            file_path = app.static_dir / relative
            if not file_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            data = file_path.read_bytes()
            mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", "%s; charset=utf-8" % mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:",
            )
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def create_server(host: str, port: int, app: Application) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(app))

