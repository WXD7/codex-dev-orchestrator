"""Shared execution machinery for every agent CLI the orchestrator drives.

The orchestrator owns the engineering guarantees, not the vendor CLI: which
sandbox or tool set a role gets, which working directory the process sees, which
environment variables are stripped, how long a run may take, and what shape the
final result must have. A new executor supplies only the pieces that are genuinely
provider-specific — how to check the CLI is installed and authenticated, how to
build the command, and how to read its event stream.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import AgentRunResult, PreflightResult

EventCallback = Callable[[str, Dict[str, Any]], None]

# Stripped from every agent subprocess. Execution must ride on the CLI's own
# subscription login; the orchestrator never hands a key to a child process.
SENSITIVE_ENV_KEYS = (
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)

MAX_STDERR_LINES = 300


def clean_environment() -> Dict[str, str]:
    env = dict(os.environ)
    for key in SENSITIVE_ENV_KEYS:
        env.pop(key, None)
    env["NO_COLOR"] = "1"
    return env


def quick(args: List[str], timeout: int = 20) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=clean_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def redacted_command(command: List[str]) -> List[str]:
    """Keep the task prompt out of the persisted command line."""
    if not command:
        return []
    result = list(command)
    result[-1] = "<task prompt: %d chars>" % len(result[-1])
    return result


class StdoutParser:
    """Turns one CLI's stdout into board events plus captured run metadata."""

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id
        self.usage: Dict[str, Any] = {}
        self.final: Dict[str, Any] = {}

    def handle(self, line: str, emit: EventCallback) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def finish(self) -> None:
        """Called once the process has exited, before the result is assembled."""


@dataclass
class StreamOutcome:
    return_code: int
    stderr_lines: List[str] = field(default_factory=list)
    timed_out: bool = False


class ProcessSupervisor:
    """Runs one agent process with a hard timeout and line-level event pumping."""

    def __init__(self, timeout_seconds: int = 3600):
        self.timeout_seconds = max(60, int(timeout_seconds))

    def run(
        self,
        command: List[str],
        cwd: Path,
        on_event: EventCallback,
        parser: StdoutParser,
        stderr_event: str,
        stdout_event: str,
    ) -> StreamOutcome:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=clean_environment(),
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
                if len(stderr_lines) > MAX_STDERR_LINES:
                    stderr_lines = stderr_lines[-MAX_STDERR_LINES:]
                on_event(stderr_event, {"line": line})
                continue
            try:
                parser.handle(line, on_event)
            except Exception:
                # A malformed line must never kill an otherwise healthy run.
                on_event(stdout_event, {"line": line})

        return_code = proc.wait() if proc.poll() is None else int(proc.returncode or 0)
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()
        parser.finish()
        return StreamOutcome(
            return_code=return_code, stderr_lines=stderr_lines, timed_out=timed_out
        )


def extract_structured_result(text: str) -> Tuple[Dict[str, Any], str]:
    """Recover the result object from an agent that cannot emit it natively.

    Codex writes the structured result to a file via --output-schema. CLIs
    without that feature answer in prose, so the contract is enforced here
    instead: find the JSON object the prompt asked for, in decreasing order of
    how well-behaved the answer was.
    """
    candidate = (text or "").strip()
    if not candidate:
        return {}, "The agent produced no output to parse"

    def loads(raw: str) -> Optional[Dict[str, Any]]:
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None

    direct = loads(candidate)
    if direct is not None:
        return direct, ""

    fence = _last_fenced_block(candidate)
    if fence:
        fenced = loads(fence)
        if fenced is not None:
            return fenced, ""

    span = _last_balanced_object(candidate)
    if span:
        scanned = loads(span)
        if scanned is not None:
            return scanned, ""

    return {}, "The agent did not return a JSON object matching the result schema"


def _last_fenced_block(text: str) -> str:
    best = ""
    marker = "```"
    index = 0
    while True:
        start = text.find(marker, index)
        if start == -1:
            break
        newline = text.find("\n", start)
        if newline == -1:
            break
        end = text.find(marker, newline)
        if end == -1:
            break
        block = text[newline + 1 : end].strip()
        if block.startswith("{"):
            best = block
        index = end + len(marker)
    return best


def _last_balanced_object(text: str) -> str:
    depth = 0
    start = -1
    best = ""
    in_string = False
    escaped = False
    for position, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = position
            depth += 1
        elif char == "}":
            if depth:
                depth -= 1
                if depth == 0 and start != -1:
                    best = text[start : position + 1]
    return best


def missing_schema_fields(final: Dict[str, Any], schema_path: Path) -> List[str]:
    """Check the required top-level fields without pulling in a JSON Schema library."""
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    required = schema.get("required") or []
    return [field_name for field_name in required if field_name not in final]


class AgentExecutor(ABC):
    """One CLI the orchestrator can hand a task to."""

    name = "agent"
    label = "Agent"

    @abstractmethod
    def preflight(self) -> PreflightResult:
        """Check the CLI is installed and signed in with subscription access."""

    @abstractmethod
    def run(
        self,
        run_id: str,
        role: str,
        worktree: Path,
        prompt: str,
        session_id: Optional[str],
        on_event: EventCallback,
        model: str = "",
    ) -> AgentRunResult:
        """Execute one task turn inside the given worktree."""
