from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import AgentRunResult, PreflightResult, READ_ONLY_ROLES


EventCallback = Callable[[str, Dict[str, Any]], None]


class CodexAgent:
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
    def _quick(args: List[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(args, text=True, capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(args, 1, "", str(exc))

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
        if not command:
            return []
        result = list(command)
        prompt = result[-1]
        result[-1] = "<task prompt: %d chars>" % len(prompt)
        return result

    @staticmethod
    def clean_environment() -> Dict[str, str]:
        env = dict(os.environ)
        for key in (
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
            "AZURE_OPENAI_API_KEY",
            "OPENAI_BASE_URL",
        ):
            env.pop(key, None)
        env["NO_COLOR"] = "1"
        return env

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
        proc = subprocess.Popen(
            command,
            cwd=str(worktree),
            env=self.clean_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )

        lines: "queue.Queue[Tuple[str, Optional[str]]]" = queue.Queue()

        def reader(source: str, stream: Any) -> None:
            try:
                for line in iter(stream.readline, ""):
                    lines.put((source, line.rstrip("\n")))
            finally:
                lines.put((source, None))

        stdout_thread = threading.Thread(
            target=reader, args=("stdout", proc.stdout), daemon=True
        )
        stderr_thread = threading.Thread(
            target=reader, args=("stderr", proc.stderr), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()

        started = time.monotonic()
        closed = set()
        stderr_lines: List[str] = []
        captured_session = session_id
        usage: Dict[str, Any] = {}
        timed_out = False

        while len(closed) < 2 or proc.poll() is None:
            if time.monotonic() - started > self.timeout_seconds:
                timed_out = True
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            try:
                source, line = lines.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                closed.add(source)
                continue
            if source == "stderr":
                stderr_lines.append(line)
                if len(stderr_lines) > 300:
                    stderr_lines = stderr_lines[-300:]
                on_event("codex.stderr", {"line": line})
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                on_event("codex.stdout", {"line": line})
                continue
            event_type = event.get("type", "codex.event")
            if event_type == "thread.started":
                captured_session = event.get("thread_id") or captured_session
            if event_type == "turn.completed":
                usage = event.get("usage") or usage
            on_event(event_type, event)

        return_code = proc.wait() if proc.poll() is None else int(proc.returncode or 0)
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()
        if timed_out:
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
            session_id=captured_session,
            usage=usage,
            stderr_tail="\n".join(stderr_lines[-300:]),
            command=self.redacted_command(command),
        )
