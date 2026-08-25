# Project instructions

- This repository is a thin AI delivery governance layer. It compiles work contracts, routes risk-selected verification, creates isolated inspector packets, adjudicates evidence, and provides integration artifacts.
- LobeHub owns the human-facing task, conversation, project, approval, memory, and reporting experience. Do not build a replacement board, chat UI, identity system, or task database.
- Kandev is the preferred development workbench for coding-agent sessions, worktrees, diffs, and review. OpenAI Symphony supplies long-running ticket-runner semantics when needed. Do not copy or fork their source into this repository; integrate through released CLI, MCP, Skill, and `WORKFLOW.md` contracts.
- GitHub Spec Kit owns specification-driven clarification, constitution, plan, and task authoring. This project compiles those human-owned artifacts into a hash-bound delivery contract; it does not recreate Spec Kit.
- GitHub Actions and repository-native tools own deterministic build, lint, type, unit, integration, security, E2E, and release evidence. Model judgment must never replace an applicable deterministic check.
- Default to one continuous owner context from investigation through implementation and one consolidated repair. Add fresh contexts only for independent failure modes selected by risk or for truly independent work; never decompose merely to imitate human job titles.
- Semantic inspectors are read-only. They do not see the owner's development transcript or peer findings before submission, do not fix product code, and do not approve their own work.
- Filter AI findings for introduced-by-change scope, concrete evidence, reproduction for high severity, and confidence of at least 0.80. Required deterministic failures always block.
- Cap automatic repair at one consolidated round in the original owner context. Repeated failure, disputed high-risk evidence, ambiguity, taste, policy, external/irreversible action, and final merge or release go to a human.
- The governance API, CLI, and MCP server are stateless. They must not open the legacy database, launch an agent, mutate tasks, own branches, impersonate approval, or contact external systems.
- Never add model API calls or accept an API key. Execution uses a locally authenticated Codex CLI/app-server. Never push, merge, deploy, publish, purchase usage, consume reset credits, delete branches, or bypass a permission boundary automatically.
- Treat quota values as exact only when they come from verified local provider telemetry. Estimates must be labeled and cannot authorize paid usage.
- The former Python board and scheduler are compatibility-only. Preserve their tests and safety invariants until a separately approved removal, but do not add new product features to that UI or task database.
- Keep the Python runtime dependency-free on Python 3.9+ and add or update `unittest` coverage for behavior changes.

## Legacy compatibility invariants

- Keep the compatibility HTTP server bound to loopback by default and keep every task execution in an isolated Git worktree.
- No executor may receive `danger-full-access`, `--dangerously-skip-permissions`, or another permission-bypass flag.
- Never commit a reviewer's edits to tracked files; a reviewer reports findings and does not fix product code.
- Preserve self-reported checks at the human gate, including an explicitly empty check list.
- A new executor implements preflight, command building, and stdout parsing only; shared supervision, timeouts, environment stripping, and prompt redaction stay in `agent_base.py`.
- An executor without native structured output must have the result contract enforced locally and fail when required fields are missing.
- Keep the legacy MCP bridge stateless. Never expose approval, review decisions, onboarding, human-authored messages, merge, or deploy tools, and extend its endpoint allowlist only with a regression test for human-only paths.
