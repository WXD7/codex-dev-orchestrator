# Kandev execution runbook

This repository delegates development sessions, worktrees, diffs, and review UI
to Kandev. The `.ai-delivery/` files remain the policy input; Kandev remains the
execution control plane.

## One-time host setup

1. Install a stable Kandev release using its supported Homebrew or npm package,
   then keep the backend bound to loopback for a single-user local install.
2. In **Settings → Agents**, rescan or install Codex and authenticate it as the
   same operating-system user that runs Kandev. Use ChatGPT subscription login,
   not a model API key.
3. Create separate Codex profiles for the writable owner and read-only
   inspectors. Keep **Auto-approve all permissions** disabled on both. Verify
   each profile's actual command preview, model, mode, sandbox, executor, and
   external-write permissions; available modes come from the installed agent
   and must not be guessed in a portable workflow file.
4. Add this governance MCP to both profiles, replacing the repository path:

```json
{
  "mcpServers": {
    "ai-delivery-governance": {
      "type": "stdio",
      "command": "python3",
      "args": [
        "/absolute/path/to/codex-dev-orchestrator-ai-native/run.py",
        "governance-mcp"
      ]
    }
  }
}
```

Kandev treats a stdio profile MCP as a per-session connection by default. This
server has no credentials and no write tools.

## Run one delivery

1. Read `.ai-delivery/contract.json`. If its status is not `ready`, stop and
   return its clarification questions to the human intake surface.
2. Read `.ai-delivery/delivery-handoff.json` and create only its `owner_task`
   with the writable owner profile in an isolated worktree.
3. Keep that owner session continuous through implementation. Capture every
   declared deterministic check as structured command/status/evidence.
4. Do not create inspectors until all required deterministic checks pass. Then
   create exactly the handoff's `inspector_tasks`, each as a new task and fresh
   session with the read-only inspector profile.
5. Submit the collected evidence to `adjudicate_delivery`. If it returns
   `repair_once`, send the one consolidated package back to the original owner
   session and rerun the complete plan once.
6. Stop at `ready_for_human_merge` or `human_decision`. Neither state authorizes
   Kandev or Codex to push, merge, open a PR, deploy, publish, or spend money.

## Workflow warning

Do not use Kandev's stock **Feature Dev** workflow unchanged for this policy. Its
documented Review step may edit trivial issues, while its PR and CI Fixup steps
can commit and push. Start with a workflow whose final steps are manual human
gates, or import a reviewed custom workflow that preserves the handoff exactly.

Kandev's portable workflow format can reset a context at a step, but it does not
by itself prove repository read-only enforcement and currently cannot express
dynamic risk-selected fan-out as a trusted portable primitive. Enforce those
properties in profiles/executors and create inspector tasks from the hash-bound
plan, rather than hard-coding a fake team of permanent roles.

## LobeHub connection

For local single-user operation, LobeHub can connect to both:

- this stdio governance MCP for pure decisions; and
- Kandev's external Streamable HTTP MCP at
  `http://127.0.0.1:<kandev-port>/mcp` for task/workflow operations.

Kandev's external MCP contains mutating configuration and task tools. Keep it on
loopback, use Kandev authentication when enabled, and require human approval for
every external write. LobeHub must not treat an MCP confirmation dialog as final
merge, release, or policy approval.

## Quota and recovery

Treat provider-usage displays as operational signals, not a billing ledger or a
guarantee that the next request will succeed. Unknown quota means cautious use;
it is not zero and must not be fabricated. Kandev's dynamic agent routing and
Office quota surfaces may be feature-flagged or experimental in a given release,
so verify them before making them a blocking dependency. Never auto-purchase
usage or consume a rate-limit reset credit.
