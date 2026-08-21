# Project instructions

- Keep the runtime dependency-free on Python 3.9+.
- Never add OpenAI API calls or accept an API key. Codex execution must use the locally authenticated `codex` CLI.
- Keep the HTTP server bound to loopback by default.
- Never let an automated task push, merge, delete branches, or use `danger-full-access`.
- Use isolated Git worktrees for task execution.
- Preserve human approval gates for architecture, security-sensitive work, and final merge decisions.
- Never commit a reviewer's edits to tracked files; a reviewer reports, it does not fix.
- Keep an agent's self-reported checks visible at the human gate, and keep an empty check list visibly empty rather than hidden.
- A new executor implements preflight, command building and stdout parsing only; process supervision, timeouts, environment stripping and prompt redaction stay shared in `agent_base.py`.
- No executor may receive `danger-full-access`, `--dangerously-skip-permissions`, or any other permission-bypass flag.
- An executor without native structured output must have the result contract enforced locally: extract the JSON, check the schema's required fields, and fail the run when they are missing.
- Keep the MCP bridge stateless: it must not open the database, start the scheduler, or launch Codex.
- Never expose approval, review decisions, project onboarding, task messages, merge or deploy as MCP tools. Return a board deep link instead.
- Extend `ALLOWED_ENDPOINTS` in `orchestrator/mcp_server.py` only together with a test asserting the human-only paths stay unreachable.
- Add or update `unittest` coverage for behavior changes.

