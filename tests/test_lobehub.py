from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from orchestrator.codex_resume_shim import reorder_resume_arguments
from orchestrator.lobehub import (
    CommandResult,
    LobeHubCLI,
    bootstrap,
    build_codex_heterogeneous_command,
    create_goal_task,
    doctor,
    integration_config,
    login,
    run_codex_heterogeneous,
    run_task_topic_with_codex,
    sanitized_environment,
)
from orchestrator.quota import QuotaSnapshot


class FakeLobeHub:
    def __init__(self, server_url="http://127.0.0.1:3210", authenticated=True):
        self.calls = []
        self.created = 0
        self.agent_created = 0
        self.agents = []
        self.command = ["lh"]
        self.environment = {}
        self.inputs = []
        self.environments = []
        self.server_url = server_url
        self.authenticated = authenticated

    def json(self, arguments):
        self.calls.append(list(arguments) + ["--json"])
        words = list(arguments)
        if words[:2] == ["provider", "list"]:
            return [{"id": "chatgpt", "name": "ChatGPT"}]
        if words[:3] == ["model", "list", "chatgpt"]:
            return [
                {"id": "gpt-5.6-sol"},
                {"id": "gpt-5.6-terra"},
                {"id": "gpt-5.6-luna"},
            ]
        if words[:2] == ["device", "list"]:
            return [{"channels": [{"channel": "cli"}, {"channel": "desktop"}]}]
        if words[:2] == ["agent", "list"]:
            return list(self.agents)
        if words[:2] == ["project", "view"]:
            return {
                "project": {"coordinatorAgentId": "agt_coordinator"},
                "agents": [],
            }
        if words[:2] == ["task", "view"]:
            return {
                "id": "task_native",
                "identifier": "DEV-1",
                "name": "Read-only smoke",
                "instruction": "Inspect the repository without editing it",
            }
        if words[:3] == ["task", "topic", "list"]:
            return [{"id": "tpc_native", "status": "failed"}]
        if words[:3] == ["task", "topic", "view"]:
            return [
                {"id": "msg_user", "role": "user"},
                {"id": "msg_assistant", "role": "assistant", "content": "..."},
            ]
        if words[:2] == ["message", "create"]:
            return {"id": "msg_heterogeneous"}
        if words[:2] == ["project", "list"]:
            return []
        if words[:3] == ["verify", "criterion", "list"]:
            return []
        if words[:3] == ["verify", "rubric", "list"]:
            return []
        return []

    def run(self, arguments, check=False, input_text=None, timeout=120):
        self.calls.append(list(arguments))
        self.inputs.append(input_text)
        self.environments.append(dict(self.environment))
        words = list(arguments)
        if words == ["--version"]:
            return CommandResult(0, "0.0.47\n", "")
        if words == ["whoami", "--json"]:
            return CommandResult(0 if self.authenticated else 1, "{}\n", "")
        if words == ["hetero", "exec", "--help"]:
            return CommandResult(0, "Agent type: codex; option --agent-arg", "")
        if words[:2] == ["project", "create"]:
            output = "✓ Created project prj_native\n"
        elif words[:3] == ["verify", "criterion", "create"]:
            self.created += 1
            output = "✓ Created criterion crt_%d\n" % self.created
        elif words[:3] == ["verify", "rubric", "create"]:
            output = "✓ Created rubric rub_native\n"
        elif words[:2] == ["agent", "create"]:
            self.agent_created += 1
            agent_id = "agt_%d" % self.agent_created
            title = words[words.index("--title") + 1]
            self.agents.append({"id": agent_id, "title": title})
            output = "✓ Created agent %s\n" % agent_id
        elif words[:3] == ["project", "task", "create"]:
            output = "✓ Created task tsk_native\n"
        elif words[:2] == ["topic", "create"]:
            output = "✓ Created topic tpc_goal\n"
        elif words[:3] == ["hetero", "exec", "--type"]:
            output = (
                '{"type":"system","data":{"sessionId":"session_native"}}\n'
                '{"type":"stream_start","data":{}}\n'
                '{"type":"stream_chunk","data":{"chunkType":"text",'
                '"content":"Working","snapshotMode":"replace"}}\n'
                '{"type":"stream_end","data":{}}\n'
                '{"type":"stream_start","data":{}}\n'
                '{"type":"stream_chunk","data":{"chunkType":"text",'
                '"content":"Inspection complete","snapshotMode":"replace"}}\n'
                '{"type":"result","data":{"status":"completed"}}\n'
            )
        else:
            output = "ok\n"
        return CommandResult(0, output, "")


class LobeHubAdapterTests(unittest.TestCase):
    def test_server_target_defaults_to_environment_override(self):
        with patch.dict(
            "os.environ", {"ORCH_LOBEHUB_SERVER": "http://127.0.0.1:3210/"}
        ):
            client = LobeHubCLI(command=["lh"])
        self.assertEqual(client.server_url, "http://127.0.0.1:3210")

    def test_lobehub_subprocess_environment_strips_api_keys(self):
        cleaned = sanitized_environment(
            {
                "PATH": "/bin",
                "OPENAI_API_KEY": "secret",
                "SOME_VENDOR_API_KEY": "secret",
                "LOBEHUB_JWT": "oauth-device-token",
            }
        )
        self.assertEqual(cleaned["PATH"], "/bin")
        self.assertEqual(cleaned["LOBEHUB_JWT"], "oauth-device-token")
        self.assertNotIn("OPENAI_API_KEY", cleaned)
        self.assertNotIn("SOME_VENDOR_API_KEY", cleaned)

    def test_read_only_heterogeneous_command_forces_safe_codex_sandbox(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = build_codex_heterogeneous_command(
                Path(temp),
                "gpt-5.6-sol",
                "read-only",
                topic_id="tpc_native",
                operation_id="op_native",
                binary="/opt/codex",
            )
        command = spec["command"]
        self.assertEqual(command[:4], ["hetero", "exec", "--type", "codex"])
        self.assertIn("--agent-arg=--sandbox", command)
        self.assertIn("--agent-arg=read-only", command)
        self.assertNotIn("--agent-arg=--approve-for-me", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", " ".join(command))
        self.assertEqual(spec["operation_id"], "op_native")

    def test_workspace_write_uses_automatic_review_without_bypass(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = build_codex_heterogeneous_command(
                Path(temp), "gpt-5.6-terra", "workspace-write", binary="codex"
            )
        command = spec["command"]
        self.assertIn("--agent-arg=workspace-write", command)
        self.assertIn("--agent-arg=--approve-for-me", command)
        self.assertNotIn("danger-full-access", " ".join(command))

    def test_resume_uses_compatibility_shim_and_reorders_exec_options(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = build_codex_heterogeneous_command(
                Path(temp),
                "gpt-5.6-sol",
                "read-only",
                session_id="session_native",
            )
        command = spec["command"]
        selected_binary = command[command.index("--command") + 1]
        self.assertTrue(selected_binary.endswith("codex_resume_shim.py"))
        self.assertTrue(spec["uses_resume_compatibility_shim"])
        self.assertEqual(
            reorder_resume_arguments(
                [
                    "exec",
                    "resume",
                    "--json",
                    "--skip-git-repo-check",
                    "--sandbox",
                    "read-only",
                    "--model",
                    "gpt-5.6-sol",
                    "session_native",
                    "-",
                ]
            ),
            [
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--model",
                "gpt-5.6-sol",
                "resume",
                "session_native",
                "-",
            ],
        )

    def test_heterogeneous_run_ingests_events_and_keeps_prompt_on_stdin(self):
        fake = FakeLobeHub()
        with tempfile.TemporaryDirectory() as temp:
            result = run_codex_heterogeneous(
                "Inspect only",
                Path(temp),
                "gpt-5.6-sol",
                "read-only",
                topic_id="tpc_native",
                assistant_message_id="msg_native",
                client=fake,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["event_count"], 7)
        self.assertEqual(result["observed_session_id"], "session_native")
        self.assertEqual(result["final_text"], "Inspection complete")
        self.assertEqual(fake.inputs[-1], "Inspect only")
        self.assertEqual(
            fake.environments[-1]["LOBEHUB_ASSISTANT_MESSAGE_ID"], "msg_native"
        )
        self.assertNotIn("LOBEHUB_ASSISTANT_MESSAGE_ID", fake.environment)
        self.assertTrue(result["operation_id"].startswith("op_codex_"))
        raw_dump_dir = Path(result["raw_dump_dir"])
        self.assertFalse(raw_dump_dir.exists())

    def test_task_topic_run_uses_frozen_task_instruction(self):
        fake = FakeLobeHub()
        with tempfile.TemporaryDirectory() as temp:
            result = run_task_topic_with_codex(
                "DEV-1",
                "tpc_native",
                Path(temp),
                "read-only",
                model="gpt-5.6-sol",
                client=fake,
            )
        self.assertEqual(result["task_id"], "task_native")
        self.assertIn("Inspect the repository without editing it", fake.inputs)
        self.assertEqual(
            result["assistant_message_id"], "msg_heterogeneous"
        )
        self.assertTrue(result["visible_message_persisted"])
        create_message = next(
            call for call in fake.calls if call[:2] == ["message", "create"]
        )
        self.assertEqual(
            create_message[create_message.index("--topic-id") + 1], "tpc_native"
        )
        edit_message = next(
            call for call in fake.calls if call[:2] == ["message", "edit"]
        )
        self.assertEqual(edit_message[-1], "Inspection complete")
        status_updates = [
            call
            for call in fake.calls
            if call[:2] == ["task", "edit"] and "--status" in call
        ]
        self.assertEqual(
            [call[call.index("--status") + 1] for call in status_updates],
            ["running", "paused"],
        )
        session_comment = next(
            call
            for call in fake.calls
            if call[:2] == ["task", "comment"]
            and "codex-session" in " ".join(call)
        )
        self.assertIn("session_native", " ".join(session_comment))

    def test_task_topic_run_rejects_unrelated_topic(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(Exception, "does not belong"):
                run_task_topic_with_codex(
                    "DEV-1",
                    "tpc_other",
                    Path(temp),
                    "read-only",
                    model="gpt-5.6-sol",
                    client=FakeLobeHub(),
                )

    def test_task_comment_can_link_released_cli_topic(self):
        class CommentLinkedFake(FakeLobeHub):
            def json(self, arguments):
                words = list(arguments)
                if words[:2] == ["task", "view"]:
                    self.calls.append(words + ["--json"])
                    return {
                        "id": "task_linked",
                        "identifier": "DEV-2",
                        "name": "Linked task",
                        "instruction": "Inspect linked topic",
                        "activities": [
                            {
                                "type": "comment",
                                "content": (
                                    "[engineering-governance execution-topic] "
                                    "tpc_linked"
                                ),
                            }
                        ],
                    }
                if words[:3] == ["task", "topic", "list"]:
                    self.calls.append(words + ["--json"])
                    return []
                return super().json(arguments)

        fake = CommentLinkedFake()
        with tempfile.TemporaryDirectory() as temp:
            result = run_task_topic_with_codex(
                "DEV-2",
                "tpc_linked",
                Path(temp),
                "read-only",
                model="gpt-5.6-sol",
                client=fake,
            )
        self.assertEqual(result["topic_link_mode"], "task-comment")

    @patch("orchestrator.lobehub.subprocess.call", return_value=0)
    def test_login_targets_self_hosted_server(self, call):
        fake = FakeLobeHub(server_url="http://127.0.0.1:3210")
        self.assertEqual(login(fake), 0)
        self.assertEqual(
            call.call_args.args[0],
            ["lh", "login", "--server", "http://127.0.0.1:3210"],
        )

    @patch("orchestrator.lobehub.subprocess.run")
    def test_doctor_treats_deferred_local_identity_as_pending_not_install_error(
        self, run
    ):
        run.return_value = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "Logged in using ChatGPT", "stderr": ""},
        )()
        fake = FakeLobeHub(authenticated=False)
        result = doctor(Path("/tmp/example-governor"), client=fake)
        self.assertTrue(result["ok"])
        self.assertFalse(result["ready_for_live_runs"])
        self.assertEqual(result["lobehub"]["deployment"], "self-hosted")
        self.assertFalse(result["lobehub"]["cloud_login_required"])
        self.assertTrue(result["pending_actions"])
        self.assertEqual(result["problems"], [])

    def test_config_points_lobehub_to_stateless_mcp(self):
        root = Path("/tmp/example-governor")
        config = integration_config(root)
        server = config["mcpServers"]["engineering-governance"]
        self.assertEqual(server["args"][-1], "mcp")
        self.assertTrue(server["args"][0].endswith("run.py"))
        self.assertIn("ORCH_CODEX_BINARY", server["env"])

    def test_bootstrap_uses_native_project_and_one_round_rubric(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".git").mkdir()
            fake = FakeLobeHub()
            result = bootstrap(
                "Demo",
                repo,
                client=fake,
                quota_snapshot=QuotaSnapshot.unknown("codex"),
            )
        self.assertEqual(result["project_id"], "prj_native")
        self.assertEqual(result["rubric_id"], "rub_native")
        self.assertEqual(result["criterion_ids"], ["crt_1", "crt_2", "crt_3", "crt_4"])
        rubric_create = next(call for call in fake.calls if call[:3] == ["verify", "rubric", "create"])
        self.assertIn("--max-repair-rounds", rubric_create)
        self.assertEqual(rubric_create[rubric_create.index("--max-repair-rounds") + 1], "1")
        self.assertEqual(sum(call[:2] == ["agent", "create"] for call in fake.calls), 0)
        coordinator_edit = next(
            call for call in fake.calls if call[:3] == ["agent", "edit", "agt_coordinator"]
        )
        self.assertNotIn("--provider", coordinator_edit)
        self.assertNotIn("--model", coordinator_edit)
        self.assertEqual(set(result["agents"]), {"coordinator", "owner", "verifier"})
        self.assertEqual(
            result["executors"]["owner"], "hetero:codex:workspace-write"
        )
        criterion_creates = [
            call for call in fake.calls if call[:3] == ["verify", "criterion", "create"]
        ]
        self.assertTrue(all(call[call.index("--type") + 1] == "agent" for call in criterion_creates))

    def test_goal_creates_one_root_task_and_native_checkpoints(self):
        fake = FakeLobeHub()
        result = create_goal_task(
            project_id="prj_native",
            name="Ship endpoint",
            goal="Add the endpoint",
            acceptance_criteria=["The user can call the endpoint and see the version"],
            risk="low",
            change_surfaces=["api"],
            client=fake,
        )
        self.assertEqual(result["task_id"], "tsk_native")
        create = next(call for call in fake.calls if call[:3] == ["project", "task", "create"])
        self.assertNotIn("--agent", create)
        self.assertIn("one continuous owner topic", create[create.index("--instruction") + 1])
        self.assertEqual(result["topic_id"], "tpc_goal")
        topic_create = next(
            call for call in fake.calls if call[:2] == ["topic", "create"]
        )
        self.assertTrue(any("Ship endpoint" in part for part in topic_create))
        contract_message = next(
            call
            for call in fake.calls
            if call[:2] == ["message", "create"] and "--role" in call
        )
        self.assertEqual(
            contract_message[contract_message.index("--role") + 1], "user"
        )
        topic_comment = next(
            call for call in fake.calls if call[:2] == ["task", "comment"]
        )
        self.assertTrue(any("tpc_goal" in part for part in topic_comment))
        checkpoint = next(call for call in fake.calls if call[:3] == ["task", "checkpoint", "set"])
        self.assertIn("--on-agent-request", checkpoint)

    def test_goal_with_no_observable_acceptance_is_not_created(self):
        fake = FakeLobeHub()
        result = create_goal_task(
            project_id="prj_native",
            name="Ambiguous",
            goal="Make it better",
            acceptance_criteria=["Tests pass"],
            risk="low",
            client=fake,
        )
        self.assertFalse(result["created"])
        self.assertEqual(result["status"], "needs_clarification")
        self.assertFalse(any(call[:3] == ["project", "task", "create"] for call in fake.calls))

    def test_goal_rejects_provider_agent_assignment(self):
        with self.assertRaisesRegex(Exception, "Provider Agent IDs"):
            create_goal_task(
                project_id="prj_native",
                name="Wrong route",
                goal="Show a visible result",
                acceptance_criteria=["The operator can see the result"],
                risk="low",
                agent_id="agt_provider",
                client=FakeLobeHub(),
            )


if __name__ == "__main__":
    unittest.main()
