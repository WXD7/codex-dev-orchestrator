from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent_base import (
    AgentExecutor,
    EventCallback,
    ProcessSupervisor,
    StdoutParser,
    clean_environment,
    quick,
    redacted_command,
)
from .models import AgentRunResult, PreflightResult, READ_ONLY_ROLES


class CodexStdoutParser(StdoutParser):
    """Codex emits one JSON event per stdout line with --json."""

    def handle(self, line: str, emit: EventCallback) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            emit("codex.stdout", {"line": line})
            return
        if not isinstance(event, dict):
            emit("codex.stdout", {"line": line})
            return
        event_type = event.get("type", "codex.event")
        if event_type == "thread.started":
            self.session_id = event.get("thread_id") or self.session_id
        if event_type == "turn.completed":
            self.usage = event.get("usage") or self.usage
        emit(event_type, event)


class CodexAgent(AgentExecutor):
    name = "codex"
    label = "Codex CLI"

    def __init__(
        self,
        binary: str,
        schema_path: Path,
        runs_dir: Path,
        model: str = "",
        timeout_seconds: int = 3600,
    ):
        self.binary = binary
        self.schema_path = Path(schema_path).resolve()
        self.runs_dir = Path(runs_dir).resolve()
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.supervisor = ProcessSupervisor(timeout_seconds)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def preflight(self) -> PreflightResult:
        problems: List[str] = []
        resolved = shutil.which(self.binary)
        if not resolved:
            return PreflightResult(
                ok=False,
                version="",
                auth_status="",
                problems=["Codex CLI not found: %s" % self.binary],
            )
        version_result = self._quick([self.binary, "--version"])
        auth_result = self._quick([self.binary, "login", "status"])
        version = (version_result.stdout or version_result.stderr).strip()
        raw_auth_status = "\n".join(
            part.strip()
            for part in (auth_result.stdout, auth_result.stderr)
            if part and part.strip()
        )
        auth_lines = [line.strip() for line in raw_auth_status.splitlines() if line.strip()]
        auth_status = next(
            (line for line in auth_lines if "logged in using chatgpt" in line.lower()),
            raw_auth_status,
        )
        if version_result.returncode != 0:
            problems.append("Could not read Codex CLI version")
        if auth_result.returncode != 0:
            problems.append("Codex CLI is not authenticated")
        elif "chatgpt" not in auth_status.lower():
            problems.append(
                "Codex must be signed in with ChatGPT subscription access, not an API key"
            )
        if not self.schema_path.is_file():
            problems.append("Agent result schema is missing")
        return PreflightResult(
            ok=not problems,
            version=version,
            auth_status=auth_status,
            problems=problems,
        )

    @staticmethod
    def _quick(args: List[str]):
        return quick(args)

    def build_command(
        self,
        role: str,
        worktree: Path,
        prompt: str,
        output_path: Path,
        session_id: Optional[str] = None,
    ) -> List[str]:
        if session_id:
            command = [
                self.binary,
                "exec",
                "resume",
                "--json",
                "--output-schema",
                str(self.schema_path),
                "-o",
                str(output_path),
            ]
            if self.model:
                command.extend(["--model", self.model])
            command.extend([session_id, prompt])
            return command

        sandbox = "read-only" if role in READ_ONLY_ROLES else "workspace-write"
        command = [
            self.binary,
            "exec",
            "--json",
            "--color",
            "never",
            "--sandbox",
            sandbox,
            "--cd",
            str(worktree),
            "--output-schema",
            str(self.schema_path),
            "-o",
            str(output_path),
        ]
        if self.model:
            command.extend(["--model", self.model])
        command.append(prompt)
        return command

    @staticmethod
    def redacted_command(command: List[str]) -> List[str]:
        return redacted_command(command)

    @staticmethod
    def clean_environment() -> Dict[str, str]:
        return clean_environment()

    def run(
        self,
        run_id: str,
        role: str,
        worktree: Path,
        prompt: str,
        session_id: Optional[str],
        on_event: EventCallback,
    ) -> AgentRunResult:
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        output_path = run_dir / "final.json"
        command = self.build_command(role, worktree, prompt, output_path, session_id)
        parser = CodexStdoutParser(session_id)
        outcome = self.supervisor.run(
            command,
            Path(worktree),
            on_event,
            parser,
            stderr_event="codex.stderr",
            stdout_event="codex.stdout",
        )

        return_code = outcome.return_code
        stderr_lines = list(outcome.stderr_lines)
        if outcome.timed_out:
            return_code = 124
            stderr_lines.append("Codex run exceeded the configured timeout")

        final: Dict[str, Any] = {}
        parse_error = ""
        if output_path.is_file():
            try:
                final = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                parse_error = "Could not parse structured Codex result: %s" % exc
        elif return_code == 0:
            parse_error = "Codex completed without writing the structured result"

        if parse_error:
            stderr_lines.append(parse_error)
            return_code = return_code or 2
        status = "complete" if return_code == 0 and final else "failed"
        return AgentRunResult(
            exit_code=return_code,
            status=status,
            final=final,
            session_id=parser.session_id,
            usage=parser.usage,
            stderr_tail="\n".join(stderr_lines[-300:]),
            command=redacted_command(command),
        )
