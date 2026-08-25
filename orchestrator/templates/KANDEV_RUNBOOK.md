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
   return its clarification questions to the human intake surface. Compile the
   answers into a delta proposal, then let the trusted controller record a human
   attestation bound to the parent contract, new contract, new plan, and the same
   Kandev task. Do not create an owner from a proposal alone.
2. Run the V2.1 preflight and persist its environment capsule. Stop if cwd, the
   real Git diff root, permissions, required commands, ports, or locks are not ready.
3. Read `.ai-delivery/runtime-protocol.json`. The trusted Kandev-side controller
   creates the signed atomic ledger and retains the control token outside every
   Agent context. A normal process exit never completes a stage by itself.
4. Read `.ai-delivery/delivery-handoff.json` and create only its `owner_task`
   with the writable owner profile in an isolated worktree.
5. Keep that owner session continuous through implementation. Capture every
   declared deterministic check as structured command/status/evidence.
6. Do not create inspectors until all required deterministic checks and evidence
   classes pass. Then create exactly the handoff's `inspector_tasks`, each as a new task and fresh
   session with the read-only inspector profile.
7. Submit structured reproduction bundles to `adjudicate_delivery`. It merges
   cross-lane findings by root cause. If it returns `repair_once`, send the one
   consolidated package back to the original owner session and rerun the complete plan once.
8. After full re-verification passes, create the handoff's `final_verifier_task`
   as a new read-only session. Do not show it the owner transcript or any prior
   finding. It must pass every must-kill case.
9. Stop at `awaiting_human_decision` or `human_decision`. Neither state authorizes
   Kandev or Codex to push, merge, open a PR, deploy, publish, or spend money.
   Build the Review Packet from the signed ledger and show it in the existing
   Kandev/LobeHub task surface.

If an artifact invariant cannot be proved, a required evidence class is missing,
the single repair does not converge, or the final blind verifier fails, append a
signed honest-stop event and pause. Never coerce the ledger to look complete.

## Operator view and heartbeats

Do not create a second dashboard. Project `build_operator_snapshot()` into the
existing Kandev task view or LobeHub page. Count unique `agent_id` values, not
terminal tabs or MCP sessions. Every lifecycle change and periodic heartbeat is
recorded with `record_agent_progress()` and includes:

- a short Chinese `display_name` and one-sentence `mission`;
- `execution_state`, `progress_summary`, `current_difficulty`, and `dependency`;
- `needs_human`, `last_heartbeat_at`, and the source platform/task/session;
- `enforcement_mode`: `shadow`, `blocking`, or `not_applicable`.

Keep execution state and delivery verdict in separate fields. A finished Agent
can still have a blocked delivery; an idle Inspector waiting for CI is not a
failed Agent. Stale heartbeats are a control-plane concern and do not by
themselves prove a product failure.

## Bad Cases and calibration

Version `.ai-delivery/bad-case-registry.json` and
`.ai-delivery/calibration-policy.json` with the repository. Candidate findings
never become must-kill cases automatically. A confirmed Case needs a named human
or expert and reproducible confirmation evidence. Hidden Cases are supplied to
read-only Inspectors and the final verifier, never to the owner.

Baseline lanes in the compiled plan retain blocking behavior. Any new or
materially changed Inspector must submit a calibration profile and remain in
shadow mode until every frozen threshold passes. Every calibration row must bind
a Case hash and include a named human labeler plus label evidence. Promotion grants only blocking
eligibility. It does not authorize approval, merge, release, or another repair
round.

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

On restart, replay the controller signatures, event hash chain, stage order, and
stored artifact invariants. Resume at the first pending stage. Never ask an Agent
to repair or regenerate a ledger signature.
