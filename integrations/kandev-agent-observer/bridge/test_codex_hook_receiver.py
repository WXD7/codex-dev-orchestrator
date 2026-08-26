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
            self.assertEqual(snapshot["bridge"]["state"], "ready")
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
