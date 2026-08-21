"""Client-agnostic MCP front door for the orchestrator.

This process owns no state. It speaks JSON-RPC 2.0 over stdio and translates a
deliberately small tool surface into calls against the local orchestrator HTTP
API, so the `serve` process stays the single owner of the scheduler, the Git
worktrees and the SQLite ledger.

The tool surface is narrower than the HTTP API on purpose. A model may read the
task graph, extend it, and run or retry tasks inside isolated worktrees. It may
never approve, reject, decide a review, register a repository, or post a message
that would appear to a downstream agent as human instruction. Those stay with a
person in the board UI; the tools hand out deep links instead.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, TextIO

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "codex-dev-orchestrator"
SERVER_VERSION = "0.1.0"

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_ID = r"[A-Za-z0-9_]{1,64}"

# Every request this process is allowed to issue. Anything absent is unreachable
# from MCP even if a future tool tries to build the path.
ALLOWED_ENDPOINTS = (
    ("GET", re.compile(r"^/api/health$")),
    ("GET", re.compile(r"^/api/projects$")),
    ("GET", re.compile(r"^/api/projects/%s/tasks$" % _ID)),
    ("GET", re.compile(r"^/api/approvals$")),
    ("GET", re.compile(r"^/api/tasks/%s$" % _ID)),
    ("GET", re.compile(r"^/api/tasks/%s/changes$" % _ID)),
    ("POST", re.compile(r"^/api/tasks$")),
    ("POST", re.compile(r"^/api/tasks/%s/dependencies$" % _ID)),
    ("POST", re.compile(r"^/api/tasks/%s/start$" % _ID)),
    ("POST", re.compile(r"^/api/tasks/%s/retry$" % _ID)),
)

HUMAN_ONLY_NOTE = (
    "Approvals, review decisions, task messages and project onboarding are "
    "human-only actions in the orchestrator board; they are not exposed as tools."
)

TASK_SUMMARY_FIELDS = (
    "id",
    "title",
    "role",
    "executor",
    "status",
    "priority",
    "parent_id",
    "requires_approval",
    "allow_delegation",
    "auto_start",
    "dependency_count",
    "child_count",
    "branch_name",
    "summary",
    "error",
    "approval_question",
    "evidence",
)

TASK_DETAIL_FIELDS = TASK_SUMMARY_FIELDS + (
    "project_id",
    "description",
    "handoff",
    "worktree_path",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
)

RUN_FIELDS = ("id", "status", "exit_code", "started_at", "completed_at", "stderr_tail")

HUMAN_GATE_STATUSES = ("waiting_approval", "review")

TASK_ROLES = ("coordinator", "planner", "implementer", "reviewer", "qa")


class OrchestratorError(Exception):
    """The orchestrator refused a request or could not be reached."""


class ToolError(Exception):
    """A tool was called with arguments this server will not act on."""


class OrchestratorClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        opener: Optional[Any] = None,
    ):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "http":
            raise ValueError("Orchestrator URL must use http, got %r" % base_url)
        host = (parsed.hostname or "").lower()
        if host not in LOOPBACK_HOSTS:
            raise ValueError(
                "Refusing to talk to a non-loopback orchestrator (%s). "
                "The control plane is local-only by design." % (parsed.hostname,)
            )
        self.base_url = ("%s://%s" % (parsed.scheme, parsed.netloc)).rstrip("/")
        self.timeout = timeout
        # No proxy handlers: loopback traffic must never leave the machine.
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )

    @staticmethod
    def is_allowed(method: str, path: str) -> bool:
        return any(
            method == allowed_method and pattern.match(path)
            for allowed_method, pattern in ALLOWED_ENDPOINTS
        )

    def request(
        self, method: str, path: str, body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if not self.is_allowed(method, path):
            raise OrchestratorError(
                "%s %s is not part of the MCP surface. %s" % (method, path, HUMAN_ONLY_NOTE)
            )
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except ValueError:
                pass
            raise OrchestratorError(
                "Orchestrator returned %d: %s" % (exc.code, detail)
            ) from exc
        except urllib.error.URLError as exc:
            raise OrchestratorError(
                "Cannot reach the orchestrator at %s (%s). "
                "Start it with `python3 run.py serve`." % (self.base_url, exc.reason)
            ) from exc
        if not raw.strip():
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"result": value}

    def board_url(self) -> str:
        return self.base_url

    def task_url(self, task_id: str) -> str:
        return "%s/#/task/%s" % (self.base_url, task_id)


def _compact(source: Dict[str, Any], fields) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key in fields:
        value = source.get(key)
        if value is None or value == "":
            continue
        result[key] = value
    return result


def _require_id(args: Dict[str, Any], key: str) -> str:
    value = str(args.get(key, "")).strip()
    if not value:
        raise ToolError("%s is required." % key)
    if not re.match(r"^%s$" % _ID, value):
        raise ToolError("%s must be an orchestrator id such as tsk_1a2b3c4d." % key)
    return value


def _optional_id(args: Dict[str, Any], key: str) -> Optional[str]:
    if not str(args.get(key, "")).strip():
        return None
    return _require_id(args, key)


def _gate(client: OrchestratorClient, task: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "task_id": task["id"],
        "title": task.get("title", ""),
        "status": task.get("status", ""),
        "question": task.get("approval_question", "") or task.get("summary", ""),
        "decide_at": client.task_url(task["id"]),
    }


def _task_detail(client: OrchestratorClient, task_id: str) -> Dict[str, Any]:
    task = client.request("GET", "/api/tasks/%s" % task_id)
    detail = _compact(task, TASK_DETAIL_FIELDS)
    detail["dependencies"] = task.get("dependencies", [])
    detail["children"] = task.get("children", [])
    detail["runs"] = [_compact(run, RUN_FIELDS) for run in task.get("runs", [])]
    detail["message_count"] = len(task.get("messages", []))
    approval = task.get("approval")
    if approval:
        detail["pending_approval"] = {
            "kind": approval.get("kind", ""),
            "question": approval.get("question", ""),
            "decide_at": client.task_url(task_id),
        }
    if task.get("status") in HUMAN_GATE_STATUSES:
        detail["waiting_on_human"] = client.task_url(task_id)
    return detail


def tool_list_projects(client: OrchestratorClient, args: Dict[str, Any]) -> Dict[str, Any]:
    payload = client.request("GET", "/api/projects")
    projects = [
        _compact(
            project,
            (
                "id",
                "name",
                "repo_path",
                "base_branch",
                "default_executor",
                "auto_start",
                "task_count",
                "running_count",
            ),
        )
        for project in payload.get("projects", [])
    ]
    return {
        "projects": projects,
        "board_url": client.board_url(),
        "note": "New projects are registered by a human in the board UI, not through MCP.",
    }


def tool_get_status(client: OrchestratorClient, args: Dict[str, Any]) -> Dict[str, Any]:
    task_id = _optional_id(args, "task_id")
    if task_id:
        return _task_detail(client, task_id)

    project_id = _optional_id(args, "project_id")
    if not project_id:
        health = client.request("GET", "/api/health")
        projects = tool_list_projects(client, {})
        return {
            "agent": health.get("agent", {}),
            "projects": projects["projects"],
            "board_url": client.board_url(),
            "executors": health.get("agent", {}).get("executors", []),
            "default_executor": health.get("agent", {}).get("default", ""),
            "hint": "Call get_status again with a project_id or task_id for detail.",
        }

    payload = client.request("GET", "/api/projects/%s/tasks" % project_id)
    tasks = payload.get("tasks", [])
    by_status: Dict[str, List[Dict[str, Any]]] = {}
    for task in tasks:
        by_status.setdefault(task.get("status", "unknown"), []).append(
            _compact(task, TASK_SUMMARY_FIELDS)
        )
    gates = [
        _gate(client, task)
        for task in tasks
        if task.get("status") in HUMAN_GATE_STATUSES
    ]
    return {
        "project_id": project_id,
        "task_count": len(tasks),
        "by_status": by_status,
        "human_gates": gates,
        "board_url": client.board_url(),
    }


def tool_get_diff(client: OrchestratorClient, args: Dict[str, Any]) -> Dict[str, Any]:
    task_id = _require_id(args, "task_id")
    max_chars = int(args.get("max_chars", 20000))
    max_chars = max(1000, min(80000, max_chars))
    changes = client.request("GET", "/api/tasks/%s/changes" % task_id)
    diff = changes.get("diff", "")
    truncated = bool(changes.get("diff_truncated"))
    if len(diff) > max_chars:
        diff = diff[:max_chars] + "\n... diff truncated at max_chars ...\n"
        truncated = True
    return {
        "task_id": task_id,
        "status": changes.get("status", ""),
        "commits": changes.get("commits", ""),
        "stat": changes.get("stat", ""),
        "diff": diff,
        "diff_truncated": truncated,
        "review_at": client.task_url(task_id),
        "note": "The branch stays local. Merging is a human decision.",
    }


def tool_list_pending_approvals(
    client: OrchestratorClient, args: Dict[str, Any]
) -> Dict[str, Any]:
    payload = client.request("GET", "/api/approvals")
    approvals = [
        {
            "task_id": item["task_id"],
            "project_name": item.get("project_name", ""),
            "task_title": item.get("task_title", ""),
            "role": item.get("task_role", ""),
            "kind": item.get("kind", ""),
            "question": item.get("question", ""),
            "created_at": item.get("created_at", ""),
            "decide_at": client.task_url(item["task_id"]),
        }
        for item in payload.get("approvals", [])
    ]
    reviews = [
        {
            "task_id": item["task_id"],
            "project_name": item.get("project_name", ""),
            "task_title": item.get("task_title", ""),
            "role": item.get("task_role", ""),
            "summary": item.get("summary", ""),
            "branch_name": item.get("branch_name", ""),
            "review_at": client.task_url(item["task_id"]),
        }
        for item in payload.get("reviews", [])
    ]
    return {
        "pending_approvals": approvals,
        "awaiting_code_review": reviews,
        "note": HUMAN_ONLY_NOTE + " Open the links above and decide there.",
    }


def tool_plan_workflow(client: OrchestratorClient, args: Dict[str, Any]) -> Dict[str, Any]:
    project_id = _require_id(args, "project_id")
    goal = str(args.get("goal", "")).strip()
    if not goal:
        raise ToolError("goal is required and should describe the full objective.")
    title = str(args.get("title", "")).strip() or goal.splitlines()[0][:80]
    task = client.request(
        "POST",
        "/api/tasks",
        {
            "project_id": project_id,
            "title": title,
            "description": goal,
            "role": "coordinator",
            "executor": str(args.get("executor", "")).strip(),
            "priority": int(args.get("priority", 60)),
            # Both are fixed: a coordinator may propose a task graph, and the
            # graph never unlocks until a human approves the plan.
            "allow_delegation": True,
            "requires_approval": True,
            "auto_start": False,
            "start_now": bool(args.get("start_now", True)),
        },
    )
    return {
        "task": _compact(task, TASK_SUMMARY_FIELDS),
        "started": bool(args.get("start_now", True)),
        "next": "The coordinator analyses the repo read-only and proposes subtasks. "
        "Its plan waits for human approval before anything downstream unlocks.",
        "approve_at": client.task_url(task["id"]),
    }


def tool_create_task(client: OrchestratorClient, args: Dict[str, Any]) -> Dict[str, Any]:
    project_id = _require_id(args, "project_id")
    title = str(args.get("title", "")).strip()
    if not title:
        raise ToolError("title is required.")
    role = str(args.get("role", "implementer")).strip()
    if role not in TASK_ROLES:
        raise ToolError("role must be one of: %s" % ", ".join(TASK_ROLES))
    dependencies = args.get("dependencies") or []
    if not isinstance(dependencies, list):
        raise ToolError("dependencies must be an array of task ids.")
    task = client.request(
        "POST",
        "/api/tasks",
        {
            "project_id": project_id,
            "parent_id": _optional_id(args, "parent_id"),
            "title": title,
            "description": str(args.get("description", "")),
            "role": role,
            "executor": str(args.get("executor", "")).strip(),
            "priority": int(args.get("priority", 50)),
            "requires_approval": bool(args.get("requires_approval", False)),
            "auto_start": bool(args.get("auto_start", False)),
            "dependencies": [str(item) for item in dependencies],
            "start_now": False,
        },
    )
    return {"task": _compact(task, TASK_SUMMARY_FIELDS), "open_at": client.task_url(task["id"])}


def tool_add_dependency(client: OrchestratorClient, args: Dict[str, Any]) -> Dict[str, Any]:
    task_id = _require_id(args, "task_id")
    depends_on = _require_id(args, "depends_on")
    task = client.request(
        "POST", "/api/tasks/%s/dependencies" % task_id, {"depends_on": depends_on}
    )
    return {
        "task": _compact(task, TASK_SUMMARY_FIELDS),
        "dependencies": task.get("dependencies", []),
    }


def tool_run_task(client: OrchestratorClient, args: Dict[str, Any]) -> Dict[str, Any]:
    task_id = _require_id(args, "task_id")
    payload = client.request("POST", "/api/tasks/%s/start" % task_id, {})
    task = payload.get("task", {})
    return {
        "queued": bool(payload.get("queued")),
        "task": _compact(task, TASK_SUMMARY_FIELDS),
        "watch_at": client.task_url(task_id),
        "note": "Execution is asynchronous in an isolated worktree. Poll get_status.",
    }


def tool_retry_task(client: OrchestratorClient, args: Dict[str, Any]) -> Dict[str, Any]:
    task_id = _require_id(args, "task_id")
    payload = client.request("POST", "/api/tasks/%s/retry" % task_id, {})
    task = payload.get("task", {})
    return {
        "queued": bool(payload.get("queued")),
        "task": _compact(task, TASK_SUMMARY_FIELDS),
        "watch_at": client.task_url(task_id),
    }


def _object(properties: Dict[str, Any], required=()) -> Dict[str, Any]:
    schema: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


_TASK_ID_PROP = {"type": "string", "description": "Task id, e.g. tsk_1a2b3c4d."}
_EXECUTOR_PROP = {
    "type": "string",
    "description": (
        "Which agent CLI runs this task, e.g. codex or claude-code. Call get_status "
        "with no arguments to see what this deployment has available. Leave empty to "
        "inherit the project default."
    ),
}
_PROJECT_ID_PROP = {"type": "string", "description": "Project id, e.g. prj_1a2b3c4d."}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "list_projects",
        "description": "List repositories a human has registered with the orchestrator.",
        "inputSchema": _object({}),
        "annotations": {"readOnlyHint": True},
        "handler": tool_list_projects,
    },
    {
        "name": "get_status",
        "description": (
            "Read the task graph. With task_id, return one task in full detail. "
            "With project_id, return tasks grouped by status plus the human gates "
            "currently blocking progress. With neither, return worker health and "
            "the project list."
        ),
        "inputSchema": _object({"project_id": _PROJECT_ID_PROP, "task_id": _TASK_ID_PROP}),
        "annotations": {"readOnlyHint": True},
        "handler": tool_get_status,
    },
    {
        "name": "get_diff",
        "description": (
            "Return the local branch diff a task produced, against the project base "
            "branch. Read-only: the branch is never pushed or merged by the orchestrator."
        ),
        "inputSchema": _object(
            {
                "task_id": _TASK_ID_PROP,
                "max_chars": {
                    "type": "integer",
                    "description": "Truncate the diff to this many characters (default 20000).",
                },
            },
            required=("task_id",),
        ),
        "annotations": {"readOnlyHint": True},
        "handler": tool_get_diff,
    },
    {
        "name": "list_pending_approvals",
        "description": (
            "List everything currently waiting on a human decision: pending approvals "
            "and tasks parked in code review. Returns deep links into the local board. "
            "There is no tool to approve or reject; a person decides in the board."
        ),
        "inputSchema": _object({}),
        "annotations": {"readOnlyHint": True},
        "handler": tool_list_pending_approvals,
    },
    {
        "name": "plan_workflow",
        "description": (
            "Start a coordinator task for a development goal. The coordinator reads the "
            "repository in a read-only sandbox and proposes a dependency graph of "
            "implementation, review and QA subtasks. The plan always waits for human "
            "approval before any subtask unlocks."
        ),
        "inputSchema": _object(
            {
                "project_id": _PROJECT_ID_PROP,
                "goal": {
                    "type": "string",
                    "description": "The full objective, including constraints and acceptance criteria.",
                },
                "title": {"type": "string", "description": "Short board title (optional)."},
                "executor": _EXECUTOR_PROP,
                "priority": {"type": "integer", "description": "0-100, default 60."},
                "start_now": {
                    "type": "boolean",
                    "description": "Queue the coordinator immediately (default true).",
                },
            },
            required=("project_id", "goal"),
        ),
        "handler": tool_plan_workflow,
    },
    {
        "name": "create_task",
        "description": (
            "Add one task to a project's graph. Created in backlog (or blocked when it "
            "has dependencies); it does not run until run_task or auto-scheduling."
        ),
        "inputSchema": _object(
            {
                "project_id": _PROJECT_ID_PROP,
                "title": {"type": "string"},
                "description": {
                    "type": "string",
                    "description": "Full instructions for the agent, including constraints.",
                },
                "role": {
                    "type": "string",
                    "enum": list(TASK_ROLES),
                    "description": "coordinator/planner run read-only; implementer/reviewer/qa get a writable worktree.",
                },
                "executor": _EXECUTOR_PROP,
                "parent_id": _TASK_ID_PROP,
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task ids that must reach done before this one becomes ready.",
                },
                "priority": {"type": "integer"},
                "requires_approval": {
                    "type": "boolean",
                    "description": "Hold the task for human approval before marking it done.",
                },
                "auto_start": {
                    "type": "boolean",
                    "description": "Queue automatically once dependencies clear.",
                },
            },
            required=("project_id", "title"),
        ),
        "handler": tool_create_task,
    },
    {
        "name": "add_dependency",
        "description": "Make task_id wait for depends_on. Cycles are rejected by the orchestrator.",
        "inputSchema": _object(
            {"task_id": _TASK_ID_PROP, "depends_on": _TASK_ID_PROP},
            required=("task_id", "depends_on"),
        ),
        "handler": tool_add_dependency,
    },
    {
        "name": "run_task",
        "description": (
            "Queue a task for execution in its own Git worktree and branch. Asynchronous: "
            "poll get_status for the outcome."
        ),
        "inputSchema": _object({"task_id": _TASK_ID_PROP}, required=("task_id",)),
        "handler": tool_run_task,
    },
    {
        "name": "retry_task",
        "description": "Re-queue a failed or blocked task, reusing its worktree and agent session.",
        "inputSchema": _object({"task_id": _TASK_ID_PROP}, required=("task_id",)),
        "handler": tool_retry_task,
    },
]

TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def public_tools() -> List[Dict[str, Any]]:
    return [
        {key: value for key, value in tool.items() if key != "handler"} for tool in TOOLS
    ]


class MCPServer:
    def __init__(self, client: OrchestratorClient):
        self.client = client
        self.initialized = False

    # -- JSON-RPC plumbing ------------------------------------------------

    @staticmethod
    def _result(request_id: Any, result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        request_id = message.get("id")
        is_notification = "id" not in message
        method = message.get("method")
        if not isinstance(method, str):
            return None if is_notification else self._error(request_id, -32600, "Invalid request")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return None if is_notification else self._error(request_id, -32602, "params must be an object")

        if method == "initialize":
            return self._result(request_id, self._initialize(params))
        if method in ("notifications/initialized", "initialized"):
            self.initialized = True
            return None
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": public_tools()})
        if method == "tools/call":
            return self._call_tool(request_id, params)
        if is_notification:
            return None
        return self._error(request_id, -32601, "Method not found: %s" % method)

    def _initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "This server is the engineering execution kernel behind the board at %s. "
                "Use it to read and extend the task graph and to run tasks in isolated Git "
                "worktrees. It never pushes, merges or deploys. %s"
            )
            % (self.client.board_url(), HUMAN_ONLY_NOTE),
        }

    def _call_tool(self, request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        tool = TOOLS_BY_NAME.get(name) if isinstance(name, str) else None
        if tool is None:
            return self._error(request_id, -32602, "Unknown tool: %s" % (name,))
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return self._error(request_id, -32602, "arguments must be an object")
        try:
            payload = tool["handler"](self.client, arguments)
        except (ToolError, OrchestratorError) as exc:
            return self._result(request_id, self._content(str(exc), is_error=True))
        except (ValueError, TypeError, KeyError) as exc:
            return self._result(
                request_id, self._content("Invalid tool arguments: %s" % exc, is_error=True)
            )
        return self._result(request_id, self._content(payload))

    @staticmethod
    def _content(payload: Any, is_error: bool = False) -> Dict[str, Any]:
        text = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False, indent=2
        )
        result: Dict[str, Any] = {"content": [{"type": "text", "text": text}]}
        if is_error:
            result["isError"] = True
        return result

    # -- stdio transport --------------------------------------------------

    def serve(self, stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
        stream_in = stdin if stdin is not None else sys.stdin
        stream_out = stdout if stdout is not None else sys.stdout
        for line in stream_in:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self._write(stream_out, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            if isinstance(message, list):
                self._write(stream_out, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Batch requests are not supported"}})
                continue
            if not isinstance(message, dict):
                self._write(stream_out, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}})
                continue
            response = self.handle(message)
            if response is not None:
                self._write(stream_out, response)
        return 0

    @staticmethod
    def _write(stream: TextIO, message: Dict[str, Any]) -> None:
        # stdout carries protocol frames only; diagnostics go to stderr.
        stream.write(json.dumps(message, ensure_ascii=False) + "\n")
        stream.flush()


def build_server(base_url: str, timeout: float = 30.0) -> MCPServer:
    return MCPServer(OrchestratorClient(base_url, timeout=timeout))


def main(argv=None) -> int:
    from .config import Config

    parser = argparse.ArgumentParser(
        prog="codex-orchestrator mcp",
        description="Expose the orchestrator to any MCP client over stdio",
    )
    parser.add_argument("--url", help="Orchestrator base URL (default: the configured host/port)")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    from pathlib import Path

    config = Config.from_env(Path(__file__).resolve().parent.parent)
    base_url = args.url or "http://%s:%d" % (config.host, config.port)
    try:
        server = build_server(base_url, timeout=args.timeout)
    except ValueError as exc:
        print("codex-orchestrator mcp: %s" % exc, file=sys.stderr)
        return 2
    print("codex-orchestrator mcp: bridging %s" % server.client.board_url(), file=sys.stderr)
    return server.serve()


if __name__ == "__main__":
    sys.exit(main())
