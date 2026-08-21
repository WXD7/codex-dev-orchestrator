"""Claude Code CLI executor.

Verified against Claude Code 2.1.237. The CLI turns out to cover both guarantees
this executor originally had to reimplement:

- `--json-schema` validates structured output, and the final `result` event
  carries the parsed object in `structured_output`. That is the direct analogue
  of Codex's `--output-schema` plus `-o`, so the result contract is native here
  too. Text extraction survives only as a fallback for a CLI that does not
  populate the field.
- `claude auth status` reports login state as JSON, so preflight can tell a
  subscription login from an API-key setup before any task runs.

Role isolation still has no `--sandbox` equivalent, so it is expressed as an
explicit tool allowlist: analysis roles simply never receive the editing tools.
"""

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
    extract_structured_result,
    missing_schema_fields,
    quick,
    redacted_command,
)
from .models import AgentRunResult, PreflightResult, READ_ONLY_ROLES

# Analysis roles get exactly the tools needed to read a repository.
READ_ONLY_TOOLS = ("Read", "Grep", "Glob")

# Implementation roles additionally get to edit and run local checks.
WRITE_TOOLS = READ_ONLY_TOOLS + (
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "Bash",
    "TodoWrite",
)

# Denied for every role. The prompt contract already forbids these; the flags are
# defence in depth so a persuaded agent still cannot reach the network or publish.
DISALLOWED_TOOLS = (
    "WebFetch",
    "WebSearch",
    "Bash(git push:*)",
    "Bash(git merge:*)",
    "Bash(git rebase:*)",
    "Bash(git remote:*)",
    "Bash(git branch -d:*)",
    "Bash(git branch -D:*)",
    "Bash(git reset --hard:*)",
)

# Must never appear in a command this orchestrator builds.
FORBIDDEN_FLAGS = (
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "bypassPermissions",
)

# An agent task must not inherit the operator's MCP servers: they are an
# uncontrolled tool surface, including network egress the allowlist cannot see.
EMPTY_MCP_CONFIG = '{"mcpServers":{}}'

RESULT_CONTRACT = (
    "\n\nSTRUCTURED RESULT\n"
    "Your final message must be the required JSON object and nothing else. It is "
    "validated against a schema; prose outside the object will fail the run.\n"
)


class ClaudeStdoutParser(StdoutParser):
    """Reads Claude Code's stream-json output, tolerating plain-text output too."""

    def __init__(self, session_id: Optional[str] = None):
        super().__init__(session_id)
        self.structured: Dict[str, Any] = {}
        self.result_text = ""
        self.assistant_text: List[str] = []
        self.plain_text: List[str] = []
        self.permission_denials: List[Any] = []
        self.is_error = False

    def handle(self, line: str, emit: EventCallback) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            self.plain_text.append(line)
            emit("claude.stdout", {"line": line})
            return
        if not isinstance(event, dict):
            self.plain_text.append(line)
            emit("claude.stdout", {"line": line})
            return

        self.session_id = event.get("session_id") or self.session_id
        event_type = str(event.get("type", "event"))
        if event_type == "result":
            self._handle_result(event, emit)
        elif event_type == "assistant":
            self.assistant_text.append(_message_text(event.get("message")))
        emit("claude.%s" % event_type, event)

    def _handle_result(self, event: Dict[str, Any], emit: EventCallback) -> None:
        self.usage = dict(event.get("usage") or {})
        if event.get("total_cost_usd") is not None:
            self.usage["total_cost_usd"] = event.get("total_cost_usd")
        if event.get("num_turns") is not None:
            self.usage["num_turns"] = event.get("num_turns")
        self.is_error = bool(event.get("is_error"))
        structured = event.get("structured_output")
        if isinstance(structured, dict):
            self.structured = structured
        result = event.get("result")
        if isinstance(result, str):
            self.result_text = result
        denials = event.get("permission_denials")
        if isinstance(denials, list) and denials:
            self.permission_denials = denials
            emit("claude.permission_denied", {"denials": denials})

    def finish(self) -> None:
        if self.structured or self.result_text.strip():
            return
        # Fall back through the least-structured sources so a CLI that answered
        # in plain text still has its result contract checked rather than ignored.
        for candidate in ("\n".join(self.assistant_text), "\n".join(self.plain_text)):
            if candidate.strip():
                self.result_text = candidate
                return


def _message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(part for part in parts if part)


class ClaudeCodeAgent(AgentExecutor):
    name = "claude-code"
    label = "Claude Code CLI"

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
        self._schema_argument = ""

    # -- readiness --------------------------------------------------------

    def preflight(self) -> PreflightResult:
        if not shutil.which(self.binary) and not Path(self.binary).is_file():
            return PreflightResult(
                ok=False,
                version="",
                auth_status="",
                problems=[
                    "Claude Code CLI not found: %s. Set ORCH_CLAUDE_BINARY to its "
                    "full path if it is not on PATH." % self.binary
                ],
            )
        problems: List[str] = []
        version_result = quick([self.binary, "--version"])
        version = (version_result.stdout or version_result.stderr).strip()
        if version_result.returncode != 0:
            problems.append("Could not read Claude Code CLI version")
        if not self.schema_path.is_file():
            problems.append("Agent result schema is missing")

        auth_status, auth_problem = self._auth_status()
        if auth_problem:
            problems.append(auth_problem)
        return PreflightResult(
            ok=not problems,
            version=version,
            auth_status=auth_status,
            problems=problems,
        )

    def _auth_status(self):
        """`claude auth status` prints JSON describing the local login."""
        result = quick([self.binary, "auth", "status"])
        raw = (result.stdout or result.stderr).strip()
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            if result.returncode != 0:
                return "", "Could not read Claude Code authentication status"
            return raw.splitlines()[0] if raw else "", ""
        if not payload.get("loggedIn"):
            return "", "Claude Code is not signed in; run `%s auth login`" % self.binary
        method = str(payload.get("authMethod", "") or "unknown")
        subscription = str(payload.get("subscriptionType", "") or "")
        status = "Logged in via %s" % method
        if subscription:
            status += " (%s)" % subscription
        return status, ""

    # -- command ----------------------------------------------------------

    def schema_argument(self) -> str:
        """The result schema as an inline argument for --json-schema.

        The CLI's validator rejects a `$schema` meta-reference it cannot resolve,
        so the draft declaration is stripped before the schema is handed over.
        """
        if self._schema_argument:
            return self._schema_argument
        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        schema.pop("$schema", None)
        self._schema_argument = json.dumps(schema, ensure_ascii=False)
        return self._schema_argument

    def build_command(
        self,
        role: str,
        worktree: Path,
        prompt: str,
        session_id: Optional[str] = None,
    ) -> List[str]:
        tools = READ_ONLY_TOOLS if role in READ_ONLY_ROLES else WRITE_TOOLS
        command = [
            self.binary,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--allowedTools",
            ",".join(tools),
            "--disallowedTools",
            ",".join(DISALLOWED_TOOLS),
            "--mcp-config",
            EMPTY_MCP_CONFIG,
            "--strict-mcp-config",
            "--json-schema",
            self.schema_argument(),
        ]
        if role not in READ_ONLY_ROLES:
            command.extend(["--permission-mode", "acceptEdits"])
        if self.model:
            command.extend(["--model", self.model])
        if session_id:
            command.extend(["--resume", session_id])
        # `--allowedTools` and friends are variadic, so the prompt needs an
        # explicit end-of-options marker or it is parsed as another tool name.
        command.extend(["--", prompt + RESULT_CONTRACT])
        assert_no_forbidden_flags(command)
        return command

    # -- execution --------------------------------------------------------

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
        command = self.build_command(role, Path(worktree), prompt, session_id)
        parser = ClaudeStdoutParser(session_id)
        outcome = self.supervisor.run(
            command,
            Path(worktree),
            on_event,
            parser,
            stderr_event="claude.stderr",
            stdout_event="claude.stdout",
        )

        return_code = outcome.return_code
        stderr_lines = list(outcome.stderr_lines)
        if outcome.timed_out:
            return_code = 124
            stderr_lines.append("Claude Code run exceeded the configured timeout")

        final: Dict[str, Any] = {}
        if not outcome.timed_out:
            final, problem = self._collect_result(parser)
            if problem:
                stderr_lines.append(problem)
                final = {}
                return_code = return_code or 2

        if parser.permission_denials:
            stderr_lines.append(
                "Claude Code denied %d tool use(s) by policy" % len(parser.permission_denials)
            )
        if parser.is_error and return_code == 0:
            return_code = 1
            stderr_lines.append("Claude Code reported an error result")

        if final:
            # Same on-disk audit trail Codex leaves behind via --output-schema.
            (run_dir / "final.json").write_text(
                json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
            )

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

    def _collect_result(self, parser: ClaudeStdoutParser):
        """Prefer the CLI's schema-validated object; parse the text only if absent."""
        final = parser.structured
        if not final:
            final, parse_error = extract_structured_result(parser.result_text)
            if parse_error:
                return {}, parse_error
        missing = missing_schema_fields(final, self.schema_path)
        if missing:
            return {}, "Structured result is missing required fields: %s" % ", ".join(missing)
        return final, ""


def assert_no_forbidden_flags(command: List[str]) -> None:
    for argument in command[:-1]:
        for flag in FORBIDDEN_FLAGS:
            if flag in argument:
                raise ValueError(
                    "Refusing to run Claude Code with %s; the orchestrator never "
                    "bypasses permissions." % flag
                )
