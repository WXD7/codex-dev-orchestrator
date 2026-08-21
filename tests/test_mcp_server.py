from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from orchestrator import mcp_server
from orchestrator.database import Database
from orchestrator.git_service import GitService
from orchestrator.mcp_server import MCPServer, OrchestratorClient
from orchestrator.models import TaskStatus
from orchestrator.web import Application, create_server
from tests.helpers import make_git_repo


class FakeScheduler:
    """Stands in for the worker pool: records submissions, runs nothing."""

    def __init__(self, db: Database):
        self.db = db
        self.submitted = []

    executor_names = ["codex", "claude-code"]

    def health(self):
        return {
            "ready": True,
            "codex_version": "fake",
            "auth_status": "Logged in using ChatGPT",
            "problems": [],
            "workers": 2,
            "queued": 0,
            "default": "codex",
            "executors": [
                {"name": "codex", "label": "Codex CLI", "ready": True},
                {"name": "claude-code", "label": "Claude Code CLI", "ready": True},
            ],
        }

    def submit(self, task_id: str) -> bool:
        self.submitted.append(task_id)
        self.db.queue_task(task_id)
        return True

    def resolve_approval(self, task_id, approved, note):  # pragma: no cover - human path
        raise AssertionError("MCP must never reach approvals")

    def review_decision(self, task_id, accepted, note):  # pragma: no cover - human path
        raise AssertionError("MCP must never reach review decisions")


class MCPServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo = make_git_repo(root)
        self.db = Database(root / "db.sqlite3")
        self.db.initialize()
        self.git = GitService(root / "worktrees")
        self.scheduler = FakeScheduler(self.db)
        app = Application(
            self.db,
            self.git,
            self.scheduler,
            Path(__file__).resolve().parent.parent / "orchestrator" / "static",
        )
        self.server = create_server("127.0.0.1", 0, app)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = "http://%s:%d" % (host, port)
        self.mcp = MCPServer(OrchestratorClient(self.base_url, timeout=10.0))
        self.project = self.db.create_project(
            name="demo", repo_path=str(self.repo), base_branch="main"
        )

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.temp.cleanup()

    # -- helpers ----------------------------------------------------------

    def call(self, name, arguments=None, request_id=1):
        response = self.mcp.handle(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        self.assertIsNotNone(response)
        return response

    def call_ok(self, name, arguments=None):
        response = self.call(name, arguments)
        result = response["result"]
        self.assertNotIn("isError", result, msg=result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])

    def call_err(self, name, arguments=None):
        result = self.call(name, arguments)["result"]
        self.assertTrue(result.get("isError"), msg=result["content"][0]["text"])
        return result["content"][0]["text"]

    # -- protocol ---------------------------------------------------------

    def test_initialize_echoes_supported_protocol_version(self):
        response = self.mcp.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
            }
        )
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "codex-dev-orchestrator")

    def test_initialize_falls_back_for_unknown_protocol_version(self):
        response = self.mcp.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}
        )
        self.assertEqual(response["result"]["protocolVersion"], mcp_server.PROTOCOL_VERSION)

    def test_notifications_get_no_response(self):
        self.assertIsNone(
            self.mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )
        self.assertTrue(self.mcp.initialized)

    def test_unknown_method_and_unknown_tool_are_protocol_errors(self):
        response = self.mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "resources/list"})
        self.assertEqual(response["error"]["code"], -32601)
        response = self.call("approve_task", {"task_id": "tsk_1"})
        self.assertEqual(response["error"]["code"], -32602)

    def test_serve_handles_malformed_lines_and_writes_one_frame_per_line(self):
        stdin = io.StringIO(
            "\n"
            "not json\n"
            + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            + "\n"
        )
        stdout = io.StringIO()
        self.mcp.serve(stdin, stdout)
        lines = [line for line in stdout.getvalue().splitlines() if line]
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["error"]["code"], -32700)
        self.assertEqual(json.loads(lines[1])["result"], {})

    # -- surface ----------------------------------------------------------

    def test_tool_surface_is_the_agreed_set(self):
        tools = self.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual(
            sorted(tool["name"] for tool in tools),
            [
                "add_dependency",
                "create_task",
                "get_diff",
                "get_status",
                "list_pending_approvals",
                "list_projects",
                "plan_workflow",
                "retry_task",
                "run_task",
            ],
        )
        for tool in tools:
            self.assertNotIn("handler", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_human_only_endpoints_are_not_reachable(self):
        client = self.mcp.client
        for method, path in (
            ("POST", "/api/tasks/tsk_1/approval"),
            ("POST", "/api/tasks/tsk_1/review"),
            ("POST", "/api/tasks/tsk_1/message"),
            ("POST", "/api/projects"),
            ("GET", "/api/tasks/tsk_1/../../etc/passwd"),
        ):
            self.assertFalse(client.is_allowed(method, path), msg=path)
            with self.assertRaises(mcp_server.OrchestratorError):
                client.request(method, path, {} if method == "POST" else None)

    def test_non_loopback_url_is_refused(self):
        with self.assertRaises(ValueError):
            OrchestratorClient("http://192.168.1.10:8765")
        with self.assertRaises(ValueError):
            OrchestratorClient("https://127.0.0.1:8765")

    # -- tools ------------------------------------------------------------

    def test_list_projects_and_cold_status(self):
        payload = self.call_ok("list_projects")
        self.assertEqual([item["id"] for item in payload["projects"]], [self.project["id"]])
        cold = self.call_ok("get_status")
        self.assertTrue(cold["agent"]["ready"])
        self.assertEqual(cold["board_url"], self.base_url)

    def test_plan_workflow_forces_delegation_and_approval(self):
        payload = self.call_ok(
            "plan_workflow",
            {
                "project_id": self.project["id"],
                "goal": "Add a read-only version endpoint",
                "requires_approval": False,
                "allow_delegation": False,
            },
        )
        task = payload["task"]
        self.assertEqual(task["role"], "coordinator")
        self.assertTrue(task["allow_delegation"])
        self.assertTrue(task["requires_approval"])
        self.assertEqual(self.scheduler.submitted, [task["id"]])
        self.assertEqual(payload["approve_at"], "%s/#/task/%s" % (self.base_url, task["id"]))

    def test_plan_workflow_requires_a_goal(self):
        self.assertIn("goal", self.call_err("plan_workflow", {"project_id": self.project["id"]}))

    def test_create_task_add_dependency_and_cycle_rejection(self):
        first = self.call_ok(
            "create_task",
            {"project_id": self.project["id"], "title": "Implement", "role": "implementer"},
        )["task"]
        second = self.call_ok(
            "create_task",
            {
                "project_id": self.project["id"],
                "title": "Review",
                "role": "reviewer",
                "dependencies": [first["id"]],
            },
        )["task"]
        self.assertEqual(second["status"], TaskStatus.BLOCKED.value)

        third = self.call_ok(
            "create_task", {"project_id": self.project["id"], "title": "QA", "role": "qa"}
        )["task"]
        linked = self.call_ok("add_dependency", {"task_id": third["id"], "depends_on": second["id"]})
        self.assertEqual([dep["id"] for dep in linked["dependencies"]], [second["id"]])
        self.assertEqual(linked["task"]["status"], TaskStatus.BLOCKED.value)

        self.assertIn(
            "cycle",
            self.call_err("add_dependency", {"task_id": first["id"], "depends_on": third["id"]}),
        )

    def test_create_task_rejects_unknown_role_and_bad_ids(self):
        self.assertIn(
            "role must be one of",
            self.call_err(
                "create_task",
                {"project_id": self.project["id"], "title": "x", "role": "architect"},
            ),
        )
        self.assertIn(
            "orchestrator id",
            self.call_err("get_diff", {"task_id": "../../secret"}),
        )

    def test_get_status_groups_tasks_and_surfaces_human_gates(self):
        task = self.db.create_task(
            project_id=self.project["id"], title="Waiting", role="implementer"
        )
        self.db.update_task(
            task["id"],
            status=TaskStatus.WAITING_APPROVAL.value,
            approval_question="Should I change the schema?",
        )
        payload = self.call_ok("get_status", {"project_id": self.project["id"]})
        self.assertEqual(payload["task_count"], 1)
        self.assertIn(TaskStatus.WAITING_APPROVAL.value, payload["by_status"])
        gate = payload["human_gates"][0]
        self.assertEqual(gate["question"], "Should I change the schema?")
        self.assertEqual(gate["decide_at"], "%s/#/task/%s" % (self.base_url, task["id"]))

    def test_get_status_for_one_task_includes_graph_and_gate_link(self):
        parent = self.db.create_task(
            project_id=self.project["id"], title="Coordinate", role="coordinator"
        )
        child = self.db.create_task(
            project_id=self.project["id"],
            parent_id=parent["id"],
            title="Implement",
            role="implementer",
            dependencies=[parent["id"]],
        )
        self.db.update_task(parent["id"], status=TaskStatus.REVIEW.value)
        payload = self.call_ok("get_status", {"task_id": parent["id"]})
        self.assertEqual(payload["id"], parent["id"])
        self.assertEqual([item["id"] for item in payload["children"]], [child["id"]])
        self.assertEqual(payload["waiting_on_human"], "%s/#/task/%s" % (self.base_url, parent["id"]))

    def test_list_pending_approvals_returns_links_only(self):
        task = self.db.create_task(
            project_id=self.project["id"], title="Plan", role="coordinator"
        )
        self.db.create_approval(task["id"], "Approve the plan?", kind="complete")
        reviewed = self.db.create_task(
            project_id=self.project["id"], title="Impl", role="implementer"
        )
        self.db.update_task(reviewed["id"], status=TaskStatus.REVIEW.value, summary="done")

        payload = self.call_ok("list_pending_approvals")
        approval = payload["pending_approvals"][0]
        self.assertEqual(approval["question"], "Approve the plan?")
        self.assertEqual(approval["decide_at"], "%s/#/task/%s" % (self.base_url, task["id"]))
        review = payload["awaiting_code_review"][0]
        self.assertEqual(review["task_id"], reviewed["id"])
        self.assertEqual(review["review_at"], "%s/#/task/%s" % (self.base_url, reviewed["id"]))
        # The tool hands out links; deciding stays a human action in the board.
        self.assertIn("human-only", payload["note"])

    def test_cold_status_lists_the_executor_roster(self):
        cold = self.call_ok("get_status")
        self.assertEqual(cold["default_executor"], "codex")
        self.assertEqual(
            [item["name"] for item in cold["executors"]], ["codex", "claude-code"]
        )

    def test_tasks_can_name_an_executor_and_unknown_names_are_rejected(self):
        task = self.call_ok(
            "create_task",
            {
                "project_id": self.project["id"],
                "title": "Review with a different model",
                "role": "reviewer",
                "executor": "claude-code",
            },
        )["task"]
        self.assertEqual(task["executor"], "claude-code")
        self.assertEqual(self.db.get_task(task["id"])["executor"], "claude-code")

        inherited = self.call_ok(
            "create_task", {"project_id": self.project["id"], "title": "Inherit"}
        )["task"]
        self.assertNotIn("executor", inherited)

        self.assertIn(
            "未知的执行器",
            self.call_err(
                "create_task",
                {"project_id": self.project["id"], "title": "x", "executor": "gpt-9"},
            ),
        )

    def test_plan_workflow_accepts_an_executor(self):
        payload = self.call_ok(
            "plan_workflow",
            {
                "project_id": self.project["id"],
                "goal": "Ship the thing",
                "executor": "claude-code",
            },
        )
        self.assertEqual(payload["task"]["executor"], "claude-code")

    def test_run_and_retry_task(self):
        task = self.call_ok(
            "create_task", {"project_id": self.project["id"], "title": "Implement"}
        )["task"]
        started = self.call_ok("run_task", {"task_id": task["id"]})
        self.assertTrue(started["queued"])
        self.assertEqual(self.scheduler.submitted, [task["id"]])

        self.assertIn("失败或阻塞", self.call_err("retry_task", {"task_id": task["id"]}))
        self.db.update_task(task["id"], status=TaskStatus.FAILED.value, queued=False)
        retried = self.call_ok("retry_task", {"task_id": task["id"]})
        self.assertTrue(retried["queued"])

    def test_get_diff_reports_branch_changes_and_honours_max_chars(self):
        task = self.db.create_task(
            project_id=self.project["id"], title="Implement", role="implementer"
        )
        prepared = self.git.prepare_worktree(
            self.project["id"], task["id"], task["title"], str(self.repo), "main"
        )
        worktree = Path(prepared["worktree_path"])
        (worktree / "feature.txt").write_text(
            "".join("line %d\n" % index for index in range(400)), encoding="utf-8"
        )
        self.git.commit_changes(str(worktree), task["id"], task["title"])
        self.db.update_task(
            task["id"],
            worktree_path=str(worktree),
            branch_name=prepared["branch_name"],
        )

        payload = self.call_ok("get_diff", {"task_id": task["id"]})
        self.assertIn("feature.txt", payload["stat"])
        self.assertFalse(payload["diff_truncated"])

        clipped = self.call_ok("get_diff", {"task_id": task["id"], "max_chars": 1000})
        self.assertTrue(clipped["diff_truncated"])
        self.assertIn("truncated", clipped["diff"])

    def test_get_diff_on_a_task_without_a_worktree_is_empty(self):
        task = self.db.create_task(
            project_id=self.project["id"], title="Not started", role="implementer"
        )
        payload = self.call_ok("get_diff", {"task_id": task["id"]})
        self.assertEqual(payload["diff"], "")

    def test_unreachable_orchestrator_reports_how_to_start_it(self):
        offline = MCPServer(OrchestratorClient("http://127.0.0.1:1", timeout=2.0))
        response = offline.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_projects", "arguments": {}},
            }
        )
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertIn("run.py serve", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
