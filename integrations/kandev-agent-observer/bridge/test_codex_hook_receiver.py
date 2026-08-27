import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("codex_hook_receiver.py")
SPEC = importlib.util.spec_from_file_location("codex_hook_receiver", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
receiver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receiver)


class CodexHookReceiverTests(unittest.TestCase):
    def test_subagent_lifecycle_is_exact_and_privacy_minimal(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            start = {
                "hook_event_name": "SubagentStart",
                "session_id": "root-1",
                "turn_id": "turn-1",
                "agent_id": "child-1",
                "agent_type": "security_auditor",
                "transcript_path": "/private/customer-transcript.jsonl",
                "prompt": "完整邮件正文：需要保密",
                "last_assistant_message": "DEEPSEEK_API_KEY 是 should-never-persist",
            }
            stop = dict(start, hook_event_name="SubagentStop", last_assistant_message="最终邮件正文")
            self.assertTrue(receiver.process_payload(start, path, "workspace-1", "2026-08-26T10:00:00Z"))
            self.assertTrue(receiver.process_payload(stop, path, "workspace-1", "2026-08-26T10:01:00Z"))

            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["bridge"]["state"], "active")
            self.assertEqual(snapshot["bridge"]["active_runs"], 1)
            self.assertEqual(snapshot["agents"][0]["display_name"], "安全审查员")
            self.assertEqual(snapshot["agents"][0]["execution_state"], "stopped")
            self.assertEqual(snapshot["edges"][0]["edge_type"], "spawn")
            self.assertEqual([event["event_type"] for event in snapshot["timeline"]], ["spawn", "stopped"])
            encoded = json.dumps(snapshot, ensure_ascii=False)
            for forbidden in (
                "customer-transcript",
                "完整邮件正文",
                "最终邮件正文",
                "should-never-persist",
                "transcript_path",
                "last_assistant_message",
                '"prompt"',
            ):
                self.assertNotIn(forbidden, encoded)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_tool_events_keep_only_allowlisted_ids_and_fixed_summaries(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            correction = {
                "hook_event_name": "PreToolUse",
                "session_id": "root-1",
                "turn_id": "turn-1",
                "tool_name": "followup_task",
                "tool_use_id": "call-1",
                "tool_input": {
                    "target": "/root/security_audit",
                    "message": "把客户邮件全文和 api_key='value with spaces' 写进报告",
                },
            }
            completed = dict(correction, hook_event_name="PostToolUse")
            completed["tool_response"] = {
                "content": [{"type": "text", "text": "客户邮件全文"}],
                "isError": False,
            }
            receiver.process_payload(correction, path, now="2026-08-26T10:00:00Z")
            receiver.process_payload(completed, path, now="2026-08-26T10:00:01Z")

            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["edges"][0]["edge_type"], "correction")
            self.assertEqual(snapshot["edges"][0]["to_agent_ids"], ["/root/security_audit"])
            self.assertEqual(snapshot["timeline"][0]["status"], "completed")
            encoded = json.dumps(snapshot, ensure_ascii=False)
            self.assertNotIn("value with spaces", encoded)
            self.assertNotIn("客户邮件全文", encoded)
            self.assertNotIn("tool_input", encoded)
            self.assertNotIn("tool_response", encoded)

    def test_spawn_request_uses_only_slug_to_derive_role(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            receiver.process_payload(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "root-1",
                    "turn_id": "turn-1",
                    "tool_name": "spawn_agent",
                    "tool_use_id": "spawn-call",
                    "tool_input": {
                        "task_name": "research_scout",
                        "message": "不能落盘的具体调研内容",
                    },
                },
                path,
                now="2026-08-26T10:00:00Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "SubagentStart",
                    "session_id": "root-1",
                    "turn_id": "turn-1",
                    "agent_id": "child-1",
                    "agent_type": "default",
                },
                path,
                now="2026-08-26T10:00:01Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "root-1",
                    "turn_id": "turn-1",
                    "tool_name": "spawn_agent",
                    "tool_use_id": "spawn-call",
                    "tool_response": {"structuredContent": {"agent_id": "child-1"}},
                },
                path,
                now="2026-08-26T10:00:02Z",
            )
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["agents"][0]["display_name"], "技术调研员")
            self.assertNotIn("不能落盘", json.dumps(snapshot, ensure_ascii=False))

    def test_recipient_alias_is_kept_as_correction_target(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            receiver.process_payload(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "root-1",
                    "turn_id": "turn-1",
                    "tool_name": "send_message",
                    "tool_use_id": "message-call",
                    "tool_input": {
                        "recipient": "/root/intent_checker",
                        "message": "不能进入快照的纠偏正文",
                    },
                },
                path,
                now="2026-08-26T10:00:00Z",
            )
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["edges"][0]["to_agent_ids"], ["/root/intent_checker"])
            self.assertNotIn("不能进入快照", json.dumps(snapshot, ensure_ascii=False))

    def test_child_tool_activity_updates_progress_without_persisting_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            receiver.process_payload(
                {
                    "hook_event_name": "SubagentStart",
                    "session_id": "root-1",
                    "turn_id": "turn-1",
                    "agent_id": "child-1",
                    "agent_type": "tester",
                },
                path,
                now="2026-08-26T10:00:00Z",
            )
            tool = {
                "session_id": "root-1",
                "turn_id": "turn-1",
                "agent_id": "child-1",
                "agent_type": "tester",
                "tool_name": "apply_patch",
                "tool_use_id": "edit-1",
                "tool_input": {"patch": "客户邮件与 DEEPSEEK_API_KEY=never-store"},
            }
            receiver.process_payload(
                dict(tool, hook_event_name="PreToolUse"),
                path,
                now="2026-08-26T10:00:01Z",
            )
            receiver.process_payload(
                dict(
                    tool,
                    hook_event_name="PostToolUse",
                    tool_response={"isError": True, "error": {"message": "客户邮件"}},
                ),
                path,
                now="2026-08-26T10:00:02Z",
            )
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            agent = snapshot["agents"][0]
            self.assertEqual(agent["progress_summary"], "已完成一项文件修改")
            self.assertIn("工具调用失败", agent["current_difficulty"])
            activity = [item for item in snapshot["timeline"] if item["event_type"] == "activity"]
            self.assertEqual(activity[0]["activity_kind"], "file_change")
            self.assertEqual(activity[0]["status"], "failed")
            encoded = json.dumps(snapshot, ensure_ascii=False)
            self.assertNotIn("客户邮件", encoded)
            self.assertNotIn("never-store", encoded)
            self.assertNotIn("tool_input", encoded)
            self.assertNotIn("tool_response", encoded)

    def test_nested_spawn_uses_calling_agent_as_parent_and_deduplicates_pre_hooks(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            spawn = {
                "hook_event_name": "PreToolUse",
                "session_id": "root-1",
                "turn_id": "turn-1",
                "agent_id": "parent-agent",
                "tool_name": "spawn_agent",
                "tool_use_id": "spawn-nested",
                "tool_input": {"task_name": "security_auditor", "message": "secret"},
            }
            receiver.process_payload(spawn, path, now="2026-08-26T10:00:00Z")
            receiver.process_payload(spawn, path, now="2026-08-26T10:00:00Z")
            receiver.process_payload(
                {
                    "hook_event_name": "SubagentStart",
                    "session_id": "root-1",
                    "turn_id": "turn-1",
                    "agent_id": "nested-child",
                    "agent_type": "default",
                },
                path,
                now="2026-08-26T10:00:01Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "root-1",
                    "turn_id": "turn-1",
                    "agent_id": "parent-agent",
                    "tool_name": "spawn_agent",
                    "tool_use_id": "spawn-nested",
                    "tool_response": {"structuredContent": {"agent_id": "nested-child"}},
                },
                path,
                now="2026-08-26T10:00:02Z",
            )
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(snapshot["pending_spawns"]), 1)
            self.assertEqual(snapshot["agents"][0]["parent_agent_id"], "parent-agent")
            self.assertEqual(snapshot["agents"][0]["root_thread_id"], "root-1")
            self.assertEqual(snapshot["agents"][0]["display_name"], "安全审查员")

    def test_parallel_spawns_bind_only_by_exact_post_tool_child_id(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            requests = (
                ("spawn-a", "research_scout", "parent-a", "child-a"),
                ("spawn-b", "security_auditor", "parent-b", "child-b"),
            )
            for tool_use_id, task_name, parent_id, _ in requests:
                receiver.process_payload(
                    {
                        "hook_event_name": "PreToolUse",
                        "session_id": "root-1",
                        "agent_id": parent_id,
                        "tool_name": "spawn_agent",
                        "tool_use_id": tool_use_id,
                        "tool_input": {"task_name": task_name, "message": "secret"},
                    },
                    path,
                    now="2026-08-26T10:00:00Z",
                )

            # Multiple unmatched requests must not be guessed from arrival order.
            receiver.process_payload(
                {
                    "hook_event_name": "SubagentStart",
                    "session_id": "root-1",
                    "agent_id": "unproved-child",
                    "agent_type": "default",
                },
                path,
                now="2026-08-26T10:00:01Z",
            )
            interim = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(interim["agents"][0]["display_name"], "实现智能体")
            self.assertEqual(interim["agents"][0]["parent_agent_id"], "root-1")
            self.assertTrue(all(not item["matched_agent_id"] for item in interim["pending_spawns"]))

            # PostToolUse supplies the exact ID for each call. Starts arriving in
            # reverse order must still retain the correct role and parent.
            for tool_use_id, _, _, child_id in reversed(requests):
                receiver.process_payload(
                    {
                        "hook_event_name": "PostToolUse",
                        "session_id": "root-1",
                        "tool_name": "spawn_agent",
                        "tool_use_id": tool_use_id,
                        "tool_response": {"structuredContent": {"agent_id": child_id}},
                    },
                    path,
                    now="2026-08-26T10:00:02Z",
                )
                receiver.process_payload(
                    {
                        "hook_event_name": "SubagentStart",
                        "session_id": "root-1",
                        "agent_id": child_id,
                        "agent_type": "default",
                    },
                    path,
                    now="2026-08-26T10:00:03Z",
                )

            snapshot = json.loads(path.read_text(encoding="utf-8"))
            agents = {item["agent_id"]: item for item in snapshot["agents"]}
            self.assertEqual(agents["child-a"]["display_name"], "技术调研员")
            self.assertEqual(agents["child-a"]["parent_agent_id"], "parent-a")
            self.assertEqual(agents["child-b"]["display_name"], "安全审查员")
            self.assertEqual(agents["child-b"]["parent_agent_id"], "parent-b")
            encoded = json.dumps(snapshot, ensure_ascii=False)
            self.assertNotIn("structuredContent", encoded)
            self.assertNotIn("tool_response", encoded)

    def test_parallel_spawn_never_assigns_last_unmatched_role_to_unproved_child(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            for tool_use_id, task_name, parent_id in (
                ("spawn-a", "research_scout", "parent-a"),
                ("spawn-b", "security_auditor", "parent-b"),
            ):
                receiver.process_payload(
                    {
                        "hook_event_name": "PreToolUse",
                        "session_id": "root-1",
                        "agent_id": parent_id,
                        "tool_name": "spawn_agent",
                        "tool_use_id": tool_use_id,
                        "tool_input": {"task_name": task_name, "message": "secret"},
                    },
                    path,
                    now="2026-08-26T10:00:00Z",
                )

            receiver.process_payload(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "root-1",
                    "tool_name": "spawn_agent",
                    "tool_use_id": "spawn-a",
                    "tool_response": {"structuredContent": {"agent_id": "child-a"}},
                },
                path,
                now="2026-08-26T10:00:01Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "SubagentStart",
                    "session_id": "root-1",
                    "agent_id": "unproved-child",
                    "agent_type": "default",
                },
                path,
                now="2026-08-26T10:00:02Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "root-1",
                    "tool_name": "spawn_agent",
                    "tool_use_id": "spawn-b",
                    "tool_response": {"structuredContent": {"agent_id": "child-b"}},
                },
                path,
                now="2026-08-26T10:00:03Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "SubagentStart",
                    "session_id": "root-1",
                    "agent_id": "child-b",
                    "agent_type": "default",
                },
                path,
                now="2026-08-26T10:00:04Z",
            )

            snapshot = json.loads(path.read_text(encoding="utf-8"))
            agents = {item["agent_id"]: item for item in snapshot["agents"]}
            self.assertEqual(agents["unproved-child"]["display_name"], "实现智能体")
            self.assertEqual(agents["unproved-child"]["parent_agent_id"], "root-1")
            self.assertEqual(agents["child-b"]["display_name"], "安全审查员")
            self.assertEqual(agents["child-b"]["parent_agent_id"], "parent-b")
            encoded = json.dumps(snapshot, ensure_ascii=False)
            self.assertNotIn("structuredContent", encoded)
            self.assertNotIn("tool_response", encoded)

    def test_spawn_post_reconciles_an_early_exact_child_start(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            receiver.process_payload(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "root-1",
                    "agent_id": "nested-parent",
                    "tool_name": "spawn_agent",
                    "tool_use_id": "spawn-exact",
                    "tool_input": {"task_name": "security_auditor"},
                },
                path,
                now="2026-08-26T10:00:00Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "SubagentStart",
                    "session_id": "root-1",
                    "agent_id": "child-exact",
                    "agent_type": "default",
                },
                path,
                now="2026-08-26T10:00:01Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "root-1",
                    "tool_name": "spawn_agent",
                    "tool_use_id": "spawn-exact",
                    "tool_response": {"agent_id": "child-exact", "message": "must not persist"},
                },
                path,
                now="2026-08-26T10:00:02Z",
            )
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            agent = snapshot["agents"][0]
            self.assertEqual(agent["display_name"], "安全审查员")
            self.assertEqual(agent["parent_agent_id"], "nested-parent")
            self.assertEqual(snapshot["edges"][0]["from_agent_id"], "nested-parent")
            self.assertEqual(snapshot["timeline"][0]["actor_agent_id"], "nested-parent")
            self.assertNotIn("must not persist", json.dumps(snapshot, ensure_ascii=False))

    def test_permission_and_session_end_prevent_false_working_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            receiver.process_payload(
                {"hook_event_name": "SessionStart", "session_id": "root-1"},
                path,
                now="2026-08-26T10:00:00Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "SubagentStart",
                    "session_id": "root-1",
                    "agent_id": "child-1",
                    "agent_type": "developer",
                },
                path,
                now="2026-08-26T10:00:01Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "PermissionRequest",
                    "session_id": "root-1",
                    "agent_id": "child-1",
                    "tool_use_id": "approval-1",
                    "tool_name": "Bash",
                    "tool_input": {"command": "do not persist"},
                },
                path,
                now="2026-08-26T10:00:02Z",
            )
            waiting = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(waiting["agents"][0]["execution_state"], "waiting_on_human")
            self.assertEqual(waiting["bridge"]["state"], "active")
            self.assertEqual(waiting["bridge"]["active_runs"], 1)

            receiver.process_payload(
                {"hook_event_name": "SessionEnd", "session_id": "root-1", "reason": "other"},
                path,
                now="2026-08-26T10:00:03Z",
            )
            ended = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(ended["agents"][0]["execution_state"], "stopped")
            self.assertEqual(ended["bridge"]["state"], "idle")
            self.assertEqual(ended["bridge"]["active_runs"], 0)
            self.assertEqual(ended["runs"][0]["execution_state"], "idle")
            self.assertIn("session_end", [event["event_type"] for event in ended["timeline"]])
            self.assertNotIn("do not persist", json.dumps(ended, ensure_ascii=False))

    def test_consecutive_completed_waits_are_folded_without_counting_pre_and_post_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            for index in range(2):
                payload = {
                    "session_id": "root-1",
                    "turn_id": "turn-1",
                    "tool_name": "wait_agent",
                    "tool_use_id": "wait-" + str(index),
                    "tool_input": {"targets": ["child-1"]},
                }
                receiver.process_payload(
                    dict(payload, hook_event_name="PreToolUse"),
                    path,
                    now="2026-08-26T10:00:0{}Z".format(index * 2),
                )
                receiver.process_payload(
                    dict(payload, hook_event_name="PostToolUse", tool_response={"isError": False}),
                    path,
                    now="2026-08-26T10:00:0{}Z".format(index * 2 + 1),
                )
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(snapshot["timeline"]), 1)
            self.assertEqual(snapshot["timeline"][0]["repeat_count"], 2)
            self.assertEqual(snapshot["timeline"][0]["status"], "completed")

    def test_failed_and_replayed_waits_are_not_folded_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            payload = {
                "session_id": "root-1",
                "turn_id": "turn-1",
                "tool_name": "wait_agent",
                "tool_use_id": "wait-failed",
                "tool_input": {"targets": ["child-1"]},
            }
            receiver.process_payload(
                dict(payload, hook_event_name="PreToolUse"), path, now="2026-08-26T10:00:00Z"
            )
            failed = dict(
                payload,
                hook_event_name="PostToolUse",
                tool_response={"isError": True, "error": {"message": "secret"}},
            )
            receiver.process_payload(failed, path, now="2026-08-26T10:00:01Z")
            receiver.process_payload(failed, path, now="2026-08-26T10:00:02Z")
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(snapshot["timeline"]), 1)
            self.assertEqual(snapshot["timeline"][0]["status"], "failed")
            self.assertNotIn("repeat_count", snapshot["timeline"][0])
            self.assertNotIn("secret", json.dumps(snapshot, ensure_ascii=False))

    def test_session_restart_isolates_old_working_state_and_late_events_do_not_revive_it(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            receiver.process_payload(
                {"hook_event_name": "SessionStart", "session_id": "root-1"},
                path,
                now="2026-08-26T10:00:00Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "SubagentStart",
                    "session_id": "root-1",
                    "agent_id": "old-child",
                    "agent_type": "tester",
                },
                path,
                now="2026-08-26T10:00:01Z",
            )
            receiver.process_payload(
                {"hook_event_name": "SessionStart", "session_id": "root-1"},
                path,
                now="2026-08-26T10:00:02Z",
            )
            restarted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(restarted["agents"][0]["execution_state"], "stopped")

            receiver.process_payload(
                {"hook_event_name": "SessionEnd", "session_id": "root-1"},
                path,
                now="2026-08-26T10:00:03Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "root-1",
                    "agent_id": "old-child",
                    "tool_name": "apply_patch",
                    "tool_use_id": "late-edit",
                    "tool_response": {"isError": False},
                },
                path,
                now="2026-08-26T10:00:04Z",
            )
            ended = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(ended["runs"][0]["execution_state"], "idle")
            self.assertEqual(ended["agents"][0]["execution_state"], "stopped")
            self.assertNotIn("late-edit", json.dumps(ended))

    def test_compaction_renews_same_run_without_stopping_children_or_clearing_pending(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            receiver.process_payload(
                {"hook_event_name": "SessionStart", "session_id": "root-1", "source": "startup"},
                path,
                now="2026-08-26T10:00:00Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "SubagentStart",
                    "session_id": "root-1",
                    "agent_id": "child-1",
                    "agent_type": "tester",
                },
                path,
                now="2026-08-26T10:00:01Z",
            )
            receiver.process_payload(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "root-1",
                    "tool_name": "spawn_agent",
                    "tool_use_id": "pending-after-compact",
                    "tool_input": {"task_name": "research_scout"},
                },
                path,
                now="2026-08-26T10:00:02Z",
            )
            receiver.process_payload(
                {"hook_event_name": "SessionStart", "session_id": "root-1", "source": "compact"},
                path,
                now="2026-08-26T10:00:03Z",
            )
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["agents"][0]["execution_state"], "working")
            self.assertEqual(snapshot["pending_spawns"][0]["tool_use_id"], "pending-after-compact")
            self.assertEqual(snapshot["runs"][0]["started_at"], "2026-08-26T10:00:00Z")
            self.assertEqual(snapshot["runs"][0]["updated_at"], "2026-08-26T10:00:03Z")

    def test_late_event_after_session_end_does_not_rewrite_loaded_bridge_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            receiver.process_payload(
                {"hook_event_name": "SessionStart", "session_id": "root-1"},
                path,
                "workspace-1",
                "2026-08-26T10:00:00Z",
            )
            receiver.process_payload(
                {"hook_event_name": "SessionEnd", "session_id": "root-1"},
                path,
                "workspace-1",
                "2026-08-26T10:00:01Z",
            )
            before = path.read_bytes()
            handled = receiver.process_payload(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "root-1",
                    "tool_name": "apply_patch",
                    "tool_use_id": "late-event",
                    "agent_id": "old-child",
                    "tool_response": {"isError": False},
                },
                path,
                "workspace-1",
                "2026-08-26T10:00:02Z",
            )
            self.assertFalse(handled)
            self.assertEqual(path.read_bytes(), before)
            snapshot = json.loads(before.decode("utf-8"))
            self.assertEqual(snapshot["generated_at"], "2026-08-26T10:00:01Z")
            self.assertEqual(snapshot["bridge"]["active_runs"], 0)
            self.assertEqual(snapshot["bridge"]["run_count"], 1)

    def test_session_start_sources_preserve_compact_but_isolate_new_generations(self):
        expectations = {
            "startup": "stopped",
            "resume": "stopped",
            "clear": "stopped",
            "compact": "working",
        }
        for source, expected_state in expectations.items():
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "hook.json"
                receiver.process_payload(
                    {"hook_event_name": "SessionStart", "session_id": "root-1", "source": "startup"},
                    path,
                    now="2026-08-26T10:00:00Z",
                )
                receiver.process_payload(
                    {
                        "hook_event_name": "SubagentStart",
                        "session_id": "root-1",
                        "agent_id": "child-live",
                        "agent_type": "tester",
                    },
                    path,
                    now="2026-08-26T10:00:01Z",
                )
                receiver.process_payload(
                    {"hook_event_name": "SessionStart", "session_id": "root-1", "source": source},
                    path,
                    now="2026-08-26T10:00:02Z",
                )
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(snapshot["agents"][0]["execution_state"], expected_state)
                self.assertEqual(snapshot["bridge"]["state"], "active")
                self.assertEqual(snapshot["bridge"]["active_runs"], 1)

    def test_late_event_for_ended_root_does_not_rewrite_other_active_root(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            for root, child in (("root-a", "child-a"), ("root-b", "child-b")):
                receiver.process_payload(
                    {"hook_event_name": "SessionStart", "session_id": root, "source": "startup"},
                    path,
                    now="2026-08-26T10:00:00Z",
                )
                receiver.process_payload(
                    {
                        "hook_event_name": "SubagentStart",
                        "session_id": root,
                        "agent_id": child,
                        "agent_type": "tester",
                    },
                    path,
                    now="2026-08-26T10:00:01Z",
                )
            receiver.process_payload(
                {"hook_event_name": "SessionEnd", "session_id": "root-a"},
                path,
                now="2026-08-26T10:00:02Z",
            )
            before = path.read_bytes()
            handled = receiver.process_payload(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "root-a",
                    "agent_id": "child-a",
                    "tool_name": "apply_patch",
                    "tool_use_id": "late-root-a",
                    "tool_response": {"isError": False},
                },
                path,
                now="2026-08-26T10:00:03Z",
            )
            self.assertFalse(handled)
            self.assertEqual(path.read_bytes(), before)
            snapshot = json.loads(before)
            self.assertEqual(snapshot["bridge"]["state"], "active")
            self.assertEqual(snapshot["bridge"]["active_runs"], 1)
            states = {item["agent_id"]: item["execution_state"] for item in snapshot["agents"]}
            self.assertEqual(states, {"child-a": "stopped", "child-b": "working"})

    def test_concurrent_hook_processes_do_not_lose_agents(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"

            def write(index):
                return receiver.process_payload(
                    {
                        "hook_event_name": "SubagentStart",
                        "session_id": "root-1",
                        "turn_id": "turn-1",
                        "agent_id": "child-" + str(index),
                        "agent_type": "tester",
                    },
                    path,
                    "workspace-1",
                    "2026-08-26T10:00:{:02d}Z".format(index),
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(write, range(20)))
            self.assertTrue(all(results))
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(snapshot["agents"]), 20)
            self.assertEqual(len(snapshot["edges"]), 20)

    def test_malformed_ids_are_not_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook.json"
            handled = receiver.process_payload(
                {
                    "hook_event_name": "SubagentStart",
                    "session_id": "root id contains spaces",
                    "agent_id": "child\nsecret",
                    "agent_type": "tester",
                },
                path,
            )
            self.assertFalse(handled)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
