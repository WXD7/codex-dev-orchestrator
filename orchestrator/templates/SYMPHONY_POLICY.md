# Symphony policy fragment

Incorporate this policy body into the repository-owned `WORKFLOW.md` after its
tracker, workspace, concurrency, Codex command, and lifecycle hooks are configured.
This fragment intentionally does not invent those deployment-specific values.

## Required artifacts

Before starting implementation, read:

- `.ai-delivery/CONSTITUTION.md`
- `.ai-delivery/technology-research.json`
- `.ai-delivery/intent-brief.json`
- `.ai-delivery/intent-inspection.json`
- `.ai-delivery/contract.json`
- `.ai-delivery/verification-plan.json`
- `.ai-delivery/runtime-protocol.json`
- `.ai-delivery/bad-case-registry.json`
- `.ai-delivery/calibration-policy.json`

First require a four-channel technology research packet and a PASS result from
its fresh read-only quality inspector. Then show the intent brief to the human and require a PASS result from a new
read-only intent inspector. The runner must then record a controller-signed human
attestation binding the research, technology strategy, intent, inspection, contract, plan and same tracker issue.
Do not create an owner while the handoff says `awaiting_intent_attestation`.

If the contract status is not `ready`, stop at the human clarification state and
report the unanswered questions. Never infer answers merely to keep the workflow
moving. A returned answer only creates a delta proposal. Before owner creation,
the trusted runner records a human attestation that binds the old contract, delta,
new contract, new plan, and the same tracker issue. This runtime signature is not
an MCP approval tool.

Before the owner starts, require a ready environment capsule covering cwd, the
real Git diff root, permissions, commands, ports, and locks. The trusted runner
owns the signed atomic checkpoint ledger; its control token must never enter a
Codex prompt. Advance only when declared artifact invariants pass.

## Owner execution

If the signed strategy enables a bounded technology race, create only the two or
three selected candidate contexts in isolated workspaces. They receive the same
contract, data, tests, evaluation dimensions and budget, and remain mutually
blind. A fresh read-only evaluator compares their submitted artifacts. Only a
subsequent controller-signed human keep/fuse/reject-all decision may create the
continuing owner; reject-all stops.

Keep one Codex owner session responsible for investigation, implementation, and
at most one consolidated repair. Work only in the issue workspace. Do not push,
merge, deploy, publish, contact external people, put API keys into prompts or
governance inputs, or change the governance policy. A human-confirmed product
runtime may read its named API key from the process environment through product
code that does not expose the value.

Run all required deterministic checks before independent semantic inspection.
A required check that cannot run is a failed gate, not a silent pass.

Use short Chinese Agent display names. On every state change and periodic
heartbeat, append a signed progress event with mission, execution state, progress,
current difficulty, dependency, human need, enforcement mode, and source
task/session. Expose the controller snapshot through the existing tracker/task
surface; do not create another task database or dashboard. Never collapse Agent
execution state and delivery verdict into one status.

## Independent inspection

Create only the verification lanes listed in `verification-plan.json`. Each
semantic lane receives a fresh, read-only context containing the work contract,
Diff, its required evidence, and relevant repository rules. Do not provide the
owner's conversation or another inspector's findings before submission.

Inspectors report structured evidence and never fix product code. Aggregate only
high-signal, introduced-by-change findings. Return one deduplicated repair package
to the original owner, rerun all evidence once, then stop for a human if it still
does not converge.

Confirmed Bad Cases require named human/expert confirmation and reproducible
evidence before they enter the hidden must-kill registry. New or materially
changed Inspectors run in shadow mode until a hash-bound Good/Bad Case calibration
profile passes every threshold. Shadow findings remain visible but cannot create
a blocker. Calibration never grants merge or release authority.

After full re-verification, create a new read-only final verifier that cannot see
the owner transcript or prior findings. It must pass all must-kill cases. A final
verifier failure stops for a human and does not authorize a second repair round.

## Human handoff

Stop for a human on contract ambiguity, disputed high-risk evidence, security or
architecture policy choices, external or irreversible actions, experience/taste,
and final merge or release. A successful agent run reaches the configured handoff
state; it does not grant its own acceptance. Build the human Review Packet from
the signed ledger, showing evidence hashes, Agent progress/difficulties,
calibration modes, and the exact decision that still belongs to the human.
