import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("codex_app_server_bridge.py")
SPEC = importlib.util.spec_from_file_location("codex_app_server_bridge", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class FakeClient:
    def __init__(self):
        self.responses = {
            ("thread/list", None): {
                "data": [
                    {
                        "id": "child-research",
                        "parentThreadId": "root-1",
                        "agentNickname": "Atlas",
                        "agentRole": "research_scout",
                        "createdAt": 100,
                        "updatedAt": 120,
                        "status": {"type": "notLoaded"},
                    },
                    {
                        "id": "child-review",
                        "parentThreadId": "root-1",
                        "agentNickname": "Sage",
                        "agentRole": "reviewer",
                        "createdAt": 101,
                        "updatedAt": 130,
                        "status": {"type": "notLoaded"},
                    },
                ],
                "nextCursor": None,
            },
            ("thread/read", "root-1"): {
                "thread": {
                    "id": "root-1",
                    "updatedAt": 140,
                    "status": {"type": "active", "activeFlags": []},
                    "turns": [
                        {
                            "status": "inProgress",
                            "startedAt": 110,
                            "completedAt": None,
                            "items": [
                                {
                                    "type": "collabAgentToolCall",
                                    "id": "spawn-1",
                                    "tool": "spawnAgent",
                                    "status": "completed",
                                    "senderThreadId": "root-1",
                                    "receiverThreadIds": ["child-research"],
                                    "prompt": "api_key=secret-should-never-be-written",
                                    "agentsStates": {
                                        "child-research": {
                                            "status": "running",
                                            "message": "正在比较两个框架；token=also-secret",
                                        }
                                    },
                                },
                                {
                                    "type": "collabAgentToolCall",
                                    "id": "correct-1",
                                    "tool": "sendInput",
                                    "status": "completed",
                                    "senderThreadId": "root-1",
                                    "receiverThreadIds": ["child-review"],
                                    "prompt": "Bearer private-token-value",
                                    "agentsStates": {},
                                },
                            ],
                        }
                    ],
                }
            },
            ("thread/read", "child-research"): {
                "thread": {
                    "id": "child-research",
                    "parentThreadId": "root-1",
                    "agentNickname": "Atlas",
                    "agentRole": "research_scout",
                    "createdAt": 100,
                    "updatedAt": 120,
                    "status": {"type": "active", "activeFlags": []},
                    "turns": [{"status": "inProgress", "items": []}],
                }
            },
            ("thread/read", "child-review"): {
                "thread": {
                    "id": "child-review",
                    "parentThreadId": "root-1",
                    "agentNickname": "Sage",
                    "agentRole": "reviewer",
                    "createdAt": 101,
                    "updatedAt": 130,
                    "status": {"type": "idle"},
                    "turns": [{"status": "completed", "items": []}],
                }
            },
        }

    def request(self, method, params=None):
        if method == "thread/list":
            return self.responses[(method, None)]
        return self.responses[(method, params["threadId"])]


class BridgeProjectionTests(unittest.TestCase):
    def test_collects_exact_agents_dag_and_correction_without_prompts(self):
        snapshot = bridge.collect_snapshot(
            FakeClient(),
            "root-1",
            cwd="/workspace",
            kandev_workspace_id="workspace-1",
        )
        self.assertEqual(snapshot["bridge"]["state"], "history_synced")
        self.assertEqual(snapshot["kandev_workspace_id"], "workspace-1")
        self.assertEqual([item["display_name"] for item in snapshot["agents"]], ["技术调研员", "独立审查员"])
        self.assertEqual(snapshot["agents"][0]["execution_state"], "historical")
        self.assertIn("不代表当前实时", snapshot["agents"][0]["progress_summary"])
        self.assertEqual([edge["edge_type"] for edge in snapshot["edges"]], ["spawn", "correction"])

        encoded = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("secret-should-never-be-written", encoded)
        self.assertNotIn("private-token-value", encoded)
        self.assertNotIn('"prompt"', encoded)

    def test_status_falls_back_to_last_persisted_turn(self):
        thread = {
            "status": {"type": "notLoaded"},
            "turns": [{"status": "failed", "items": []}],
        }
        self.assertEqual(bridge.status_from_thread(thread), "failed")

    def test_spawn_prompt_is_not_used_or_persisted_for_role_inference(self):
        client = FakeClient()
        client.responses[("thread/list", None)]["data"][0]["agentRole"] = ""
        client.responses[("thread/read", "child-research")]["thread"]["agentRole"] = ""
        root = client.responses[("thread/read", "root-1")]["thread"]
        root["turns"][0]["items"][0]["prompt"] = "请负责技术调研与框架比较；token=also-secret"
        root["turns"][0]["items"].append(
            {
                "type": "collabAgentToolCall",
                "id": "wait-1",
                "tool": "wait",
                "status": "completed",
                "senderThreadId": "root-1",
                "receiverThreadIds": ["child-research"],
            }
        )
        root["turns"][0]["items"].append(
            {
                "type": "collabAgentToolCall",
                "id": "wait-2",
                "tool": "wait",
                "status": "completed",
                "senderThreadId": "root-1",
                "receiverThreadIds": ["child-research"],
            }
        )
        snapshot = bridge.collect_snapshot(client, "root-1")
        self.assertEqual(snapshot["agents"][0]["display_name"], "执行智能体")
        self.assertNotIn("wait", [edge["edge_type"] for edge in snapshot["edges"]])
        self.assertIn("wait", [event["event_type"] for event in snapshot["timeline"]])
        waits = [event for event in snapshot["timeline"] if event["event_type"] == "wait"]
        self.assertEqual(len(waits), 1)
        self.assertEqual(waits[0]["repeat_count"], 2)
        self.assertNotIn("技术调研与框架比较", json.dumps(snapshot, ensure_ascii=False))

    def test_failure_snapshot_retains_last_good_agents(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "snapshot.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [{"agent_id": "child-1"}],
                        "edges": [],
                        "timeline": [],
                        "bridge": {"last_success_at": "2026-08-26T00:00:00Z"},
                    }
                ),
                encoding="utf-8",
            )
            snapshot = bridge.failure_snapshot(path, "root-1", "/workspace", RuntimeError("boom"))
        self.assertEqual(snapshot["bridge"]["state"], "stale")
        self.assertEqual(snapshot["agents"][0]["agent_id"], "child-1")
        self.assertEqual(snapshot["bridge"]["last_success_at"], "2026-08-26T00:00:00Z")

    def test_atomic_writer_uses_private_file_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "snapshot.json"
            bridge.atomic_write_json(path, {"ok": True})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_app_server_environment_excludes_api_keys_and_tokens(self):
        filtered = bridge.minimal_subprocess_env(
            {
                "HOME": "/safe/home",
                "PATH": "/usr/bin",
                "DEEPSEEK_API_KEY": "must-not-pass",
                "OPENAI_API_KEY": "must-not-pass-either",
                "GITHUB_TOKEN": "also-secret",
            }
        )
        self.assertEqual(filtered, {"HOME": "/safe/home", "PATH": "/usr/bin"})


if __name__ == "__main__":
    unittest.main()
