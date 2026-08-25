"""Small adapter for the unmodified LobeHub Desktop/CLI distribution."""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .governance import compile_work_contract
from .quota import (
    CodexQuotaProbe,
    QuotaSnapshot,
    choose_model_tier,
    decision_reason,
    quota_mode,
)


LOBE_APP = Path("/Applications/LobeHub.app")
LOBE_JS = LOBE_APP / "Contents/Resources/bin/lobe-cli.js"
LOBE_ELECTRON = LOBE_APP / "Contents/MacOS/LobeHub"
CODEX_APP_BINARY = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
LOBEHUB_CLOUD_SERVER = "https://app.lobehub.com"
CODEX_HETEROGENEOUS_TYPE = "codex"
SAFE_CODEX_SANDBOXES = ("read-only", "workspace-write")
DANGEROUS_CODEX_ARGUMENTS = (
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "danger-full-access",
)
EXECUTION_TOPIC_MARKER = "[engineering-governance execution-topic]"
CODEX_SESSION_MARKER = "[engineering-governance codex-session]"


def sanitized_environment(source: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Keep LobeHub OAuth/device credentials while removing model API keys."""

    environment = dict(source or os.environ)
    for key in list(environment):
        upper = key.upper()
        if upper.endswith("_API_KEY") or upper in {
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "OPENAI_BASE_URL",
            "ANTHROPIC_BASE_URL",
        }:
            environment.pop(key, None)
    return environment


class LobeHubError(RuntimeError):
    pass


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _new_operation_id() -> str:
    return "op_codex_%s" % uuid.uuid4().hex


def _validate_identifier(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned or any(character.isspace() for character in cleaned):
        raise ValueError("%s must be a non-empty identifier without whitespace" % label)
    return cleaned


class LobeHubCLI:
    """Invoke `lh` without requiring a separately installed Node runtime."""

    def __init__(
        self,
        command: Optional[Sequence[str]] = None,
        server_url: Optional[str] = None,
    ):
        self.command = list(command or self.discover_command())
        self.server_url = (
            server_url
            or os.environ.get("ORCH_LOBEHUB_SERVER", "").strip()
            or LOBEHUB_CLOUD_SERVER
        ).rstrip("/")
        self.environment = sanitized_environment()
        self.environment["LOBEHUB_SERVER"] = self.server_url
        if self.command and Path(self.command[0]) == LOBE_ELECTRON:
            self.environment["ELECTRON_RUN_AS_NODE"] = "1"

    @staticmethod
    def discover_command() -> List[str]:
        override = os.environ.get("ORCH_LOBEHUB_CLI", "").strip()
        if override:
            return [override]
        installed = shutil.which("lh")
        if installed:
            return [installed]
        if LOBE_ELECTRON.is_file() and LOBE_JS.is_file():
            return [str(LOBE_ELECTRON), str(LOBE_JS)]
        raise LobeHubError(
            "LobeHub CLI was not found. Install LobeHub Desktop from https://lobehub.com/downloads."
        )

    def run(
        self,
        arguments: Sequence[str],
        check: bool = False,
        input_text: Optional[str] = None,
        timeout: int = 120,
    ) -> CommandResult:
        completed = subprocess.run(
            self.command + list(arguments),
            input=input_text,
            capture_output=True,
            text=True,
            env=self.environment,
            timeout=timeout,
        )
        result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise LobeHubError(detail or "LobeHub CLI command failed")
        return result

    def json(self, arguments: Sequence[str]) -> Any:
        result = self.run(list(arguments) + ["--json"], check=True)
        raw = result.stdout.strip()
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise LobeHubError("LobeHub CLI returned non-JSON output: %s" % raw[:500]) from exc


def codex_binary() -> str:
    override = os.environ.get("ORCH_CODEX_BINARY", "").strip()
    if override:
        return override
    found = shutil.which("codex")
    if found:
        return found
    return str(CODEX_APP_BINARY) if CODEX_APP_BINARY.is_file() else "codex"


def codex_resume_shim_binary() -> str:
    return str((Path(__file__).resolve().parent / "codex_resume_shim.py"))


def build_codex_heterogeneous_command(
    cwd: Path,
    model: str,
    sandbox: str,
    topic_id: str = "",
    operation_id: str = "",
    session_id: str = "",
    binary: str = "",
    raw_dump_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build the only permitted LobeHub-to-Codex subscription execution path.

    LobeHub Desktop 2.2.14 injects Codex's dangerous bypass flag when callers
    omit an execution-mode argument.  Supplying ``--sandbox`` explicitly is
    therefore a security invariant, not an optional convenience.
    """

    selected_sandbox = sandbox.strip()
    if selected_sandbox not in SAFE_CODEX_SANDBOXES:
        raise ValueError(
            "sandbox must be one of: %s" % ", ".join(SAFE_CODEX_SANDBOXES)
        )
    repository = Path(cwd).expanduser().resolve()
    if not repository.is_dir():
        raise ValueError("cwd must be an existing directory: %s" % repository)
    selected_model = model.strip()
    if not selected_model:
        raise ValueError("model is required")

    selected_topic = topic_id.strip()
    selected_operation = operation_id.strip()
    if selected_topic:
        selected_topic = _validate_identifier(selected_topic, "topic_id")
        selected_operation = (
            _validate_identifier(selected_operation, "operation_id")
            if selected_operation
            else _new_operation_id()
        )
    elif selected_operation:
        selected_operation = _validate_identifier(selected_operation, "operation_id")

    selected_session = session_id.strip()
    selected_binary = binary.strip() or codex_binary()
    if selected_session and not binary.strip():
        selected_binary = codex_resume_shim_binary()
    command = [
        "hetero",
        "exec",
        "--type",
        CODEX_HETEROGENEOUS_TYPE,
        "--prompt",
        "-",
        "--cwd",
        str(repository),
        "--command",
        selected_binary,
        "--model",
        selected_model,
        "--agent-arg=--sandbox",
        "--agent-arg=%s" % selected_sandbox,
        "--render",
        "jsonl",
    ]
    if selected_sandbox == "workspace-write":
        # Automatic review is narrower than blanket approval bypass and keeps
        # the Codex workspace-write sandbox active for unattended delivery.
        command.append("--agent-arg=--approve-for-me")
    if selected_session:
        command.extend(
            ["--resume", _validate_identifier(selected_session, "session_id")]
        )
    if selected_topic:
        command.extend(
            ["--topic", selected_topic, "--operation-id", selected_operation]
        )
    elif selected_operation:
        command.extend(["--operation-id", selected_operation])
    if raw_dump_dir is not None:
        raw_root = Path(raw_dump_dir).expanduser().resolve()
        if not raw_root.is_dir():
            raise ValueError("raw_dump_dir must be an existing directory")
        command.extend(["--raw-dump", str(raw_root)])

    flattened = " ".join(command)
    for dangerous in DANGEROUS_CODEX_ARGUMENTS:
        if dangerous in flattened:
            raise LobeHubError("Unsafe Codex argument was rejected: %s" % dangerous)
    if "--agent-arg=--sandbox" not in command:
        raise LobeHubError("Codex heterogeneous execution requires an explicit sandbox")
    return {
        "command": command,
        "cwd": str(repository),
        "model": selected_model,
        "sandbox": selected_sandbox,
        "topic_id": selected_topic or None,
        "operation_id": selected_operation or None,
        "session_id": selected_session or None,
        "uses_resume_compatibility_shim": bool(selected_session and not binary.strip()),
        "raw_dump_dir": str(raw_dump_dir) if raw_dump_dir is not None else None,
        "api_keys_required": False,
    }


def _jsonl_events(output: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _event_value(payload: Any, keys: Sequence[str]) -> str:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = _event_value(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _event_value(value, keys)
            if found:
                return found
    return ""


def _final_text_from_events(events: Sequence[Dict[str, Any]]) -> str:
    text = ""
    for event in events:
        event_type = str(event.get("type") or "")
        if event_type == "stream_start":
            text = ""
            continue
        if event_type != "stream_chunk":
            continue
        data = event.get("data")
        if not isinstance(data, dict) or data.get("subagent"):
            continue
        if str(data.get("chunkType") or "") != "text":
            continue
        content = data.get("content")
        if not isinstance(content, str) or not content:
            continue
        if data.get("snapshotMode") == "replace":
            text = content
        else:
            text += content
    return text.strip()


def _raw_codex_session_id(raw_dump_dir: Path) -> str:
    for output_path in sorted(Path(raw_dump_dir).glob("*/*.stdout.jsonl")):
        try:
            with output_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "thread.started":
                        session_id = event.get("thread_id")
                        if isinstance(session_id, str) and session_id.strip():
                            return session_id.strip()
        except OSError:
            continue
    return ""


def run_codex_heterogeneous(
    prompt: str,
    cwd: Path,
    model: str,
    sandbox: str,
    topic_id: str = "",
    operation_id: str = "",
    session_id: str = "",
    assistant_message_id: str = "",
    client: Optional[LobeHubCLI] = None,
    timeout: int = 3600,
) -> Dict[str, Any]:
    """Execute local ChatGPT-authenticated Codex and optionally ingest into a Topic."""

    if not prompt.strip():
        raise ValueError("prompt is required")
    selected_assistant_message = assistant_message_id.strip()
    if topic_id.strip() and not selected_assistant_message:
        raise ValueError(
            "assistant_message_id is required for visible LobeHub Topic ingest"
        )
    if selected_assistant_message:
        selected_assistant_message = _validate_identifier(
            selected_assistant_message, "assistant_message_id"
        )
    lobe = client or LobeHubCLI()
    with tempfile.TemporaryDirectory(prefix="codex-hetero-raw-") as raw_directory:
        raw_dump_dir = Path(raw_directory)
        specification = build_codex_heterogeneous_command(
            cwd=cwd,
            model=model,
            sandbox=sandbox,
            topic_id=topic_id,
            operation_id=operation_id,
            session_id=session_id,
            raw_dump_dir=raw_dump_dir,
        )
        previous_assistant_message = lobe.environment.get(
            "LOBEHUB_ASSISTANT_MESSAGE_ID"
        )
        previous_real_binary = lobe.environment.get("ORCH_CODEX_REAL_BINARY")
        if selected_assistant_message:
            lobe.environment["LOBEHUB_ASSISTANT_MESSAGE_ID"] = selected_assistant_message
        if specification["uses_resume_compatibility_shim"]:
            lobe.environment["ORCH_CODEX_REAL_BINARY"] = codex_binary()
        try:
            result = lobe.run(
                specification["command"],
                input_text=prompt,
                timeout=timeout,
            )
        finally:
            if previous_assistant_message is None:
                lobe.environment.pop("LOBEHUB_ASSISTANT_MESSAGE_ID", None)
            else:
                lobe.environment[
                    "LOBEHUB_ASSISTANT_MESSAGE_ID"
                ] = previous_assistant_message
            if previous_real_binary is None:
                lobe.environment.pop("ORCH_CODEX_REAL_BINARY", None)
            else:
                lobe.environment["ORCH_CODEX_REAL_BINARY"] = previous_real_binary
        events = _jsonl_events(result.stdout)
        final_text = _final_text_from_events(events)
        observed_session = ""
        for event in events:
            observed_session = _event_value(
                event, ("sessionId", "session_id", "thread_id", "threadId")
            ) or observed_session
        observed_session = observed_session or _raw_codex_session_id(raw_dump_dir)
    response = dict(specification)
    response.update(
        {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "events": events,
            "event_count": len(events),
            "final_text": final_text,
            "observed_session_id": observed_session or None,
            "continuation_session_id": observed_session or session_id.strip() or None,
            "stderr": result.stderr.strip(),
            "assistant_message_id": selected_assistant_message or None,
        }
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise LobeHubError(detail or "Codex heterogeneous execution failed")
    return response


def run_task_topic_with_codex(
    task_id: str,
    topic_id: str,
    cwd: Path,
    sandbox: str,
    model: str = "",
    operation_id: str = "",
    session_id: str = "",
    client: Optional[LobeHubCLI] = None,
    timeout: int = 3600,
) -> Dict[str, Any]:
    """Run a frozen LobeHub Task contract in one of that Task's existing Topics."""

    selected_task = _validate_identifier(task_id, "task_id")
    lobe = client or LobeHubCLI()
    task = lobe.json(["task", "view", selected_task])
    if not isinstance(task, dict):
        raise LobeHubError("LobeHub returned no Task detail for %s" % selected_task)
    instruction = str(task.get("instruction") or "").strip()
    if not instruction:
        raise LobeHubError("Task %s has no frozen instruction" % selected_task)
    return run_task_topic_prompt_with_codex(
        task_id=selected_task,
        topic_id=topic_id,
        cwd=cwd,
        prompt=instruction,
        sandbox=sandbox,
        model=model,
        operation_id=operation_id,
        session_id=session_id,
        client=lobe,
        timeout=timeout,
        persist_prompt=False,
        manage_task_status=True,
        task=task,
    )


def task_topic_is_linked(
    task: Dict[str, Any],
    task_id: str,
    topic_id: str,
    client: LobeHubCLI,
) -> str:
    """Return ``native``/``task-comment`` for a released-CLI Task Topic link."""

    topics = _items(client.json(["task", "topic", "list", task_id]))
    if any(str(topic.get("id") or "") == topic_id for topic in topics):
        return "native"
    for activity in task.get("activities") or []:
        if not isinstance(activity, dict):
            continue
        content = str(activity.get("content") or "")
        if EXECUTION_TOPIC_MARKER in content and topic_id in content.split():
            return "task-comment"
    return ""


def run_task_topic_prompt_with_codex(
    task_id: str,
    topic_id: str,
    cwd: Path,
    prompt: str,
    sandbox: str,
    model: str = "",
    operation_id: str = "",
    session_id: str = "",
    assistant_message_id: str = "",
    client: Optional[LobeHubCLI] = None,
    timeout: int = 3600,
    persist_prompt: bool = True,
    manage_task_status: bool = True,
    task: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one visible, prepared turn in a Task-linked Topic.

    The frozen-contract wrapper above remains the public one-shot command.  The
    governed control loop uses this lower-level form for repair and read-only
    falsification prompts.  Those prompts are persisted as normal user messages
    before execution, so LobeHub still owns the complete visible conversation.
    """

    selected_task = _validate_identifier(task_id, "task_id")
    selected_topic = _validate_identifier(topic_id, "topic_id")
    selected_prompt = prompt.strip()
    if not selected_prompt:
        raise ValueError("prompt is required")
    lobe = client or LobeHubCLI()
    task_payload = task or lobe.json(["task", "view", selected_task])
    if not isinstance(task_payload, dict):
        raise LobeHubError("LobeHub returned no Task detail for %s" % selected_task)
    link_mode = task_topic_is_linked(
        task_payload, selected_task, selected_topic, lobe
    )
    if not link_mode:
        raise LobeHubError(
            "Topic %s does not belong to Task %s" % (selected_topic, selected_task)
        )
    prompt_message_id = ""
    if persist_prompt:
        prompt_message = lobe.json(
            [
                "message", "create", "--role", "user", "--content",
                selected_prompt, "--topic-id", selected_topic,
            ]
        )
        prompt_message_id = (
            str(prompt_message.get("id") or "")
            if isinstance(prompt_message, dict)
            else ""
        )
        if not prompt_message_id:
            raise LobeHubError(
                "Could not persist the governed prompt in Topic %s" % selected_topic
            )
    selected_assistant_message = assistant_message_id.strip()
    if not selected_assistant_message:
        placeholder = lobe.json(
            [
                "message", "create", "--role", "assistant", "--content", "...",
                "--topic-id", selected_topic,
            ]
        )
        selected_assistant_message = (
            str(placeholder.get("id") or "")
            if isinstance(placeholder, dict)
            else ""
        )
    if not selected_assistant_message:
        raise LobeHubError(
            "Could not create the heterogeneous assistant message in Topic %s"
            % selected_topic
        )
    selected_model = model.strip()
    if not selected_model:
        role = "verifier" if sandbox == "read-only" else "owner"
        selected_model = str(quota_model_policy()["models"][role])
    if manage_task_status:
        lobe.run(
            ["task", "edit", selected_task, "--status", "running"],
            check=True,
        )
    try:
        result = run_codex_heterogeneous(
            prompt=selected_prompt,
            cwd=cwd,
            model=selected_model,
            sandbox=sandbox,
            topic_id=selected_topic,
            operation_id=operation_id,
            session_id=session_id,
            assistant_message_id=selected_assistant_message,
            client=lobe,
            timeout=timeout,
        )
        final_text = str(result.get("final_text") or "").strip()
        if not final_text:
            raise LobeHubError(
                "Codex completed but the heterogeneous event stream had no final text"
            )
        lobe.run(
            [
                "message", "edit", selected_assistant_message,
                "--content", final_text,
            ],
            check=True,
        )
        continuation_session = str(
            result.get("continuation_session_id") or ""
        ).strip()
        if continuation_session:
            session_comment = "%s %s topic %s" % (
                CODEX_SESSION_MARKER,
                continuation_session,
                selected_topic,
            )
            lobe.run(
                ["task", "comment", selected_task, "--message", session_comment],
                check=True,
            )
        if manage_task_status:
            lobe.run(
                ["task", "edit", selected_task, "--status", "paused"],
                check=True,
            )
    except Exception:
        if manage_task_status:
            lobe.run(
                ["task", "edit", selected_task, "--status", "failed"],
                check=False,
            )
        raise
    result["visible_message_persisted"] = True
    result["task_status"] = "paused" if manage_task_status else "managed-by-loop"
    result["task_id"] = str(task_payload.get("id") or selected_task)
    result["task_identifier"] = str(
        task_payload.get("identifier") or selected_task
    )
    result["task_name"] = str(task_payload.get("name") or "")
    result["topic_link_mode"] = link_mode
    result["prompt_message_id"] = prompt_message_id or None
    return result


def desktop_version() -> str:
    info = LOBE_APP / "Contents/Info.plist"
    if not info.is_file():
        return ""
    try:
        with info.open("rb") as handle:
            return str(plistlib.load(handle).get("CFBundleShortVersionString", ""))
    except (OSError, ValueError):
        return ""


def integration_config(project_root: Path) -> Dict[str, Any]:
    return {
        "mcpServers": {
            "engineering-governance": {
                "command": sys.executable,
                "args": [str((Path(project_root) / "run.py").resolve()), "mcp"],
                "env": {"ORCH_CODEX_BINARY": codex_binary()},
            }
        }
    }


def _model_for_tier(tier: str) -> str:
    return {
        "high": "gpt-5.6-sol",
        "balanced": "gpt-5.6-terra",
        "economy": "gpt-5.6-luna",
    }.get(tier, "gpt-5.6-terra")


def quota_model_policy(snapshot: Optional[QuotaSnapshot] = None) -> Dict[str, Any]:
    observed = snapshot or CodexQuotaProbe(codex_binary(), ttl_seconds=5).read(force=True)
    mode = quota_mode(observed)
    roles = {
        "coordinator": ("planner", 65),
        "owner": ("implementer", 85),
        "verifier": ("reviewer", 70),
    }
    models = {
        name: _model_for_tier(choose_model_tier(observed, role, priority))
        for name, (role, priority) in roles.items()
    }
    return {
        "mode": mode,
        "models": models,
        "reason": decision_reason(observed, mode),
        "defer_until": observed.reset_at if mode == "blocked" else None,
        "quota": observed.to_dict(),
    }


def doctor(project_root: Path, client: Optional[LobeHubCLI] = None) -> Dict[str, Any]:
    problems: List[str] = []
    pending_actions: List[str] = []
    lobe_ready = False
    lobe_authenticated = False
    lobe_cli_version = ""
    lobe_server = LOBEHUB_CLOUD_SERVER
    heterogeneous_codex_available = False
    online_device_channels: List[str] = []
    try:
        lobe = client or LobeHubCLI()
        lobe_server = lobe.server_url
        version = lobe.run(["--version"])
        lobe_cli_version = version.stdout.strip()
        lobe_ready = version.returncode == 0
        identity = lobe.run(["whoami", "--json"])
        lobe_authenticated = identity.returncode == 0
        heterogeneous_help = lobe.run(["hetero", "exec", "--help"])
        heterogeneous_text = "%s\n%s" % (
            heterogeneous_help.stdout,
            heterogeneous_help.stderr,
        )
        heterogeneous_codex_available = (
            heterogeneous_help.returncode == 0
            and "codex" in heterogeneous_text.lower()
            and "--agent-arg" in heterogeneous_text
        )
        if not heterogeneous_codex_available:
            problems.append("LobeHub CLI has no usable heterogeneous Codex harness")
        if not lobe_authenticated:
            if lobe_server == LOBEHUB_CLOUD_SERVER:
                pending_actions.append(
                    "LobeHub Cloud 登录已按当前策略暂缓；若启用云端任务事实源，再运行 "
                    "`python3 run.py login`。"
                )
            else:
                pending_actions.append(
                    "自托管 LobeHub 尚未建立本地身份；Project/Task/Topic 持久化启用后，"
                    "针对该本地服务器运行 `python3 run.py login`。"
                )
        else:
            devices = _items(lobe.json(["device", "list"]))
            for device in devices:
                for channel in device.get("channels") or []:
                    if isinstance(channel, dict) and channel.get("channel"):
                        online_device_channels.append(str(channel["channel"]))
    except (LobeHubError, OSError, subprocess.SubprocessError) as exc:
        problems.append(str(exc))

    binary = codex_binary()
    codex_authenticated = False
    codex_status = ""
    try:
        result = subprocess.run(
            [binary, "login", "status"], capture_output=True, text=True, timeout=30
        )
        codex_status = (result.stdout or result.stderr).strip()
        codex_authenticated = result.returncode == 0 and "chatgpt" in codex_status.lower()
        if not codex_authenticated:
            pending_actions.append(
                "Codex CLI 的当前订阅凭据未通过检查；正式运行前需要恢复该 CLI 的 "
                "ChatGPT 登录。"
            )
    except (OSError, subprocess.SubprocessError) as exc:
        problems.append("Codex CLI 检查失败：%s" % exc)

    return {
        "ok": lobe_ready and not problems,
        "ready_for_live_runs": (
            lobe_ready
            and lobe_authenticated
            and codex_authenticated
            and heterogeneous_codex_available
            and not problems
        ),
        "architecture": "lobehub-native-with-recoverable-governance-loop",
        "lobehub": {
            "installed": LOBE_APP.is_dir() or bool(shutil.which("lh")),
            "desktop_version": desktop_version(),
            "cli_version": lobe_cli_version,
            "server": lobe_server,
            "deployment": (
                "cloud" if lobe_server == LOBEHUB_CLOUD_SERVER else "self-hosted"
            ),
            "authenticated": lobe_authenticated,
            "cloud_login_required": False,
            "local_identity_required_for_persistent_tasks": True,
            "chatgpt_provider_required_for_codex_execution": False,
            "heterogeneous_codex_available": heterogeneous_codex_available,
            "online_device_channels": sorted(set(online_device_channels)),
        },
        "codex": {
            "binary": binary,
            "authenticated_with_chatgpt": codex_authenticated,
            "status": codex_status,
            "execution_path": "lobehub-heterogeneous-codex-cli",
            "safe_sandboxes": list(SAFE_CODEX_SANDBOXES),
            "dangerous_bypass_forbidden": True,
        },
        "mcp_config": integration_config(project_root),
        "api_keys_required": False,
        "legacy_board_is_primary": False,
        "pending_actions": pending_actions,
        "problems": problems,
    }


def login(client: Optional[LobeHubCLI] = None) -> int:
    lobe = client or LobeHubCLI()
    # The device-code flow is intentionally interactive and is the only point
    # at which a person must establish the task owner's identity. For a
    # self-hosted server this is a local account, not a LobeHub Cloud login.
    return subprocess.call(
        lobe.command + ["login", "--server", lobe.server_url],
        env=lobe.environment,
    )


def _items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "projects", "criteria", "rubrics", "models", "agents", "devices", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _title(item: Dict[str, Any]) -> str:
    return str(item.get("title") or item.get("name") or "").strip()


def _created_id(output: str) -> str:
    matches = re.findall(
        r"(?:Created|created)\s+(?:project|criterion|rubric|task|agent|topic)\s+([A-Za-z0-9_-]+)",
        output,
    )
    if not matches:
        raise LobeHubError("Could not identify the object created by LobeHub: %s" % output[:500])
    return matches[-1]


def bootstrap(
    project_name: str,
    repo_path: Path,
    identifier: str = "DEV",
    client: Optional[LobeHubCLI] = None,
    quota_snapshot: Optional[QuotaSnapshot] = None,
) -> Dict[str, Any]:
    """Create the native project plus stateless Codex execution policy and rubric."""

    name = project_name.strip()
    if not name:
        raise ValueError("project_name is required")
    repository = Path(repo_path).expanduser().resolve()
    if not (repository / ".git").exists():
        raise ValueError("repo_path must be a Git repository: %s" % repository)
    lobe = client or LobeHubCLI()
    model_policy = quota_model_policy(quota_snapshot)
    selected_models = dict(model_policy["models"])

    projects = _items(lobe.json(["project", "list"]))
    project = next((item for item in projects if _title(item) == name), None)
    created: List[str] = []
    if project:
        project_id = str(project.get("id") or "")
    else:
        result = lobe.run(
            [
                "project",
                "create",
                "--identifier",
                identifier.strip().upper() or "DEV",
                "--name",
                name,
                "--description",
                "Continuity-first engineering with local Codex and evidence-driven acceptance.",
                "--visibility",
                "private",
            ],
            check=True,
        )
        project_id = _created_id(result.stdout)
        created.append("project")

    project_detail = lobe.json(["project", "view", project_id])
    coordinator_id = ""
    if isinstance(project_detail, dict):
        project_payload = project_detail.get("project") or {}
        if isinstance(project_payload, dict):
            coordinator_id = str(project_payload.get("coordinatorAgentId") or "")

    coordinator_role = (
        "You coordinate an AI-native engineering project. Freeze observable outcomes and "
        "negative scope before execution; use the engineering-governance MCP; preserve one "
        "continuous owner context; create fresh contexts only for independent falsification; "
        "treat CI as a precondition, not Acceptance; allow one consolidated repair; never "
        "simulate human approval or perform push, merge, deploy, publish, purchase or credit reset."
    )
    if coordinator_id:
        lobe.run(
            [
                "agent", "edit", coordinator_id,
                "--system-role", coordinator_role,
            ],
            check=True,
        )

    # Native LobeHub Agents use Provider credentials.  They are deliberately
    # not used as delivery executors here: owner and verifier are execution
    # policies for `lh hetero exec --type codex`, backed by the already logged
    # in local Codex CLI subscription.
    owner_id = "hetero:codex:workspace-write"
    verifier_id = "hetero:codex:read-only"

    criterion_specs = [
        (
            "[Engineering Governor v2] Observable user outcome",
            "agent",
            "auto_repair",
            {
                "method": "Inspect the real user or operator surface named by the frozen work contract.",
                "expected": "Every agreed outcome has concrete surface evidence in the current immutable round.",
            },
        ),
        (
            "[Engineering Governor v2] Scope and prohibited behavior",
            "agent",
            "auto_repair",
            {
                "method": "Compare observable behavior and the changeset with non-goals and prohibitions.",
                "expected": "No prohibited behavior, unrelated expansion or unapproved contract drift remains.",
            },
        ),
        (
            "[Engineering Governor v2] Diagnosable failure and recovery",
            "agent",
            "manual",
            {
                "method": "Exercise the declared failure, observation and recovery path on the real surface.",
                "expected": "An operator can recognize failure and follow the declared recovery or rollback path.",
            },
        ),
        (
            "[Engineering Governor v2] Independent counterexample search",
            "agent",
            "manual",
            {
                "method": "Use a fresh read-only context to search for evidence-backed counterexamples.",
                "expected": "No blocking outcome, safety, experience or operational counterexample remains.",
            },
        ),
    ]
    existing_criteria = _items(lobe.json(["verify", "criterion", "list"]))
    criterion_ids: List[str] = []
    for title, verifier_type, on_fail, verifier_config in criterion_specs:
        existing = next((item for item in existing_criteria if _title(item) == title), None)
        if existing:
            criterion_ids.append(str(existing.get("id") or ""))
            continue
        result = lobe.run(
            [
                "verify",
                "criterion",
                "create",
                "--title",
                title,
                "--type",
                verifier_type,
                "--on-fail",
                on_fail,
                "--config",
                json.dumps(verifier_config, ensure_ascii=False),
            ],
            check=True,
        )
        criterion_ids.append(_created_id(result.stdout))
        created.append("criterion")

    rubric_title = "[Engineering Governor v2] Outcome-oriented delivery"
    rubrics = _items(lobe.json(["verify", "rubric", "list"]))
    rubric = next((item for item in rubrics if _title(item) == rubric_title), None)
    if rubric:
        rubric_id = str(rubric.get("id") or "")
    else:
        result = lobe.run(
            [
                "verify",
                "rubric",
                "create",
                "--title",
                rubric_title,
                "--description",
                "Observable outcomes, negative scope, recovery evidence and fresh-context falsification; one repair round.",
                "--max-repair-rounds",
                "1",
            ],
            check=True,
        )
        rubric_id = _created_id(result.stdout)
        created.append("rubric")

    lobe.run(
        ["verify", "rubric", "set-criteria", rubric_id] + criterion_ids,
        check=True,
    )
    return {
        "project_id": project_id,
        "rubric_id": rubric_id,
        "criterion_ids": criterion_ids,
        "agents": {
            "coordinator": coordinator_id,
            "owner": None,
            "verifier": None,
        },
        "executors": {
            "owner": owner_id,
            "verifier": verifier_id,
            "transport": "lobehub-heterogeneous-codex-cli",
        },
        "models": selected_models,
        "quota_policy": model_policy,
        "created": created,
        "repo_path": str(repository),
        "next": "Create a frozen goal contract, run the owner, then publish immutable Acceptance evidence.",
    }


def create_goal_task(
    project_id: str,
    name: str,
    goal: str,
    acceptance_criteria: Sequence[str],
    risk: str = "medium",
    agent_id: str = "",
    subjective: bool = False,
    irreversible: bool = False,
    external_effects: bool = False,
    security_sensitive: bool = False,
    user_outcome: str = "",
    non_goals: Sequence[str] = (),
    prohibited_behaviors: Sequence[str] = (),
    assumptions: Sequence[str] = (),
    constraints: Sequence[str] = (),
    change_surfaces: Sequence[str] = (),
    security_boundaries: Sequence[str] = (),
    rollback_plan: str = "",
    observability_signals: Sequence[str] = (),
    performance_budgets: Sequence[str] = (),
    client: Optional[LobeHubCLI] = None,
) -> Dict[str, Any]:
    """Create one root LobeHub Task with the compiled operating contract."""

    if not project_id.strip() or not name.strip():
        raise ValueError("project_id and name are required")
    contract = compile_work_contract(
        {
            "goal": goal,
            "user_outcome": user_outcome or goal,
            "acceptance_criteria": list(acceptance_criteria),
            "non_goals": list(non_goals),
            "prohibited_behaviors": list(prohibited_behaviors),
            "assumptions": list(assumptions),
            "constraints": list(constraints),
            "change_surfaces": list(change_surfaces),
            "security_boundaries": list(security_boundaries),
            "rollback_plan": rollback_plan,
            "observability_signals": list(observability_signals),
            "performance_budgets": list(performance_budgets),
            "risk": risk,
            "subjective": subjective,
            "irreversible": irreversible,
            "external_effects": external_effects,
            "security_sensitive": security_sensitive,
        }
    )
    if contract["status"] != "ready":
        return {
            "created": False,
            "status": contract["status"],
            "contract": contract,
            "next": "Answer the blocking clarification questions, then compile the goal again.",
        }
    lobe = client or LobeHubCLI()
    if agent_id.strip():
        raise LobeHubError(
            "LobeHub Provider Agent IDs are not valid Codex subscription executors. "
            "Create the Task unassigned, then run it through the heterogeneous Codex harness."
        )
    command = [
        "project",
        "task",
        "create",
        project_id.strip(),
        "--name",
        name.strip(),
        "--instruction",
        contract["task_instruction"],
    ]
    result = lobe.run(command, check=True)
    task_id = _created_id(result.stdout)
    settings = contract["lobe_settings"]
    lobe.run(
        [
            "task",
            "checkpoint",
            "set",
            task_id,
            "--on-agent-request",
            "true",
            "--topic-before",
            str(settings["pause_before_each_topic"]).lower(),
            "--topic-after",
            str(settings["pause_after_each_topic"]).lower(),
        ],
        check=True,
    )
    topic_result = lobe.run(
        [
            "topic",
            "create",
            "--title",
            "%s %s" % (name.strip(), task_id),
        ],
        check=True,
    )
    topic_id = _created_id(topic_result.stdout)
    user_message = lobe.json(
        [
            "message",
            "create",
            "--role",
            "user",
            "--content",
            contract["task_instruction"],
            "--topic-id",
            topic_id,
        ]
    )
    user_message_id = (
        str(user_message.get("id") or "")
        if isinstance(user_message, dict)
        else ""
    )
    if not user_message_id:
        raise LobeHubError("Could not create the frozen contract message in %s" % topic_id)
    topic_comment = "%s %s" % (EXECUTION_TOPIC_MARKER, topic_id)
    lobe.run(
        ["task", "comment", task_id, "--message", topic_comment],
        check=True,
    )
    return {
        "task_id": task_id,
        "topic_id": topic_id,
        "contract_message_id": user_message_id,
        "agent_id": None,
        "executor": "hetero:codex:workspace-write",
        "contract": contract,
        "created": True,
        "next": (
            "Execute the linked LobeHub Topic with the safe heterogeneous Codex harness."
        ),
    }
