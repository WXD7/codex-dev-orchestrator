# Symphony policy fragment

Incorporate this policy body into the repository-owned `WORKFLOW.md` after its
tracker, workspace, concurrency, Codex command, and lifecycle hooks are configured.
This fragment intentionally does not invent those deployment-specific values.

## Required artifacts

Before starting implementation, read:

- `.ai-delivery/CONSTITUTION.md`
- `.ai-delivery/contract.json`
- `.ai-delivery/verification-plan.json`

If the contract status is not `ready`, stop at the human clarification state and
report the unanswered questions. Never infer answers merely to keep the workflow
moving.

## Owner execution

Keep one Codex owner session responsible for investigation, implementation, and
at most one consolidated repair. Work only in the issue workspace. Do not push,
merge, deploy, publish, contact external people, accept API keys, or change the
governance policy.

Run all required deterministic checks before independent semantic inspection.
A required check that cannot run is a failed gate, not a silent pass.

## Independent inspection

Create only the verification lanes listed in `verification-plan.json`. Each
semantic lane receives a fresh, read-only context containing the work contract,
Diff, its required evidence, and relevant repository rules. Do not provide the
owner's conversation or another inspector's findings before submission.

Inspectors report structured evidence and never fix product code. Aggregate only
high-signal, introduced-by-change findings. Return one deduplicated repair package
to the original owner, rerun all evidence once, then stop for a human if it still
does not converge.

## Human handoff

Stop for a human on contract ambiguity, disputed high-risk evidence, security or
architecture policy choices, external or irreversible actions, experience/taste,
and final merge or release. A successful agent run reaches the configured handoff
state; it does not grant its own acceptance.
