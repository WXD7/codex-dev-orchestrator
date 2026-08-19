# Project instructions

- Keep the runtime dependency-free on Python 3.9+.
- Never add OpenAI API calls or accept an API key. Codex execution must use the locally authenticated `codex` CLI.
- Keep the HTTP server bound to loopback by default.
- Never let an automated task push, merge, delete branches, or use `danger-full-access`.
- Use isolated Git worktrees for task execution.
- Preserve human approval gates for architecture, security-sensitive work, and final merge decisions.
- Add or update `unittest` coverage for behavior changes.

