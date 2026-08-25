---
name: govern-engineering-delivery
description: Govern an engineering delivery in LobeHub from frozen outcomes through evidence-backed verification, one repair, and accountable human acceptance. Use for autonomous implementation, repair, review, or release-readiness work managed by this project; do not use for ordinary questions that do not create or assess a delivery.
---

# Govern Engineering Delivery

Use LobeHub as the task, topic, execution and Acceptance source of truth. This skill supplies decision discipline; it does not create a second task ledger or simulate a human approval.

## Before material work

Call `compile_engineering_goal` with the observable user outcome, acceptance criteria, explicit non-goals/prohibitions, change surfaces, risk and relevant safety/operational boundaries.

- If it returns `needs_clarification`, ask only the blocking questions and stop material implementation.
- Freeze the returned contract hash. Use `compare_engineering_contracts` before accepting later scope or outcome changes; protected-field drift requires a human decision.
- Keep investigation, implementation and the single repair round in the most compatible existing owner Topic.

Read [references/delivery-loop.md](references/delivery-loop.md) before running or resuming a delivery. Read [references/quality-constitution.md](references/quality-constitution.md) when selecting gates or judging release readiness.

## Verification

Call `build_verification_plan` and execute its deterministic preconditions before model review. Do not turn tests, build, lint, type checks or scanners into Acceptance checks.

Only create the independent, fresh-context lanes named by the plan. They are strictly read-only and return structured findings; they never repair the product. Read [references/verification-lanes.md](references/verification-lanes.md) before launching a lane and [references/finding-protocol.md](references/finding-protocol.md) before reporting or aggregating findings.

Call `aggregate_verification_findings` after all required lanes report. Low-confidence or evidence-free review noise is not a repair instruction. Send one deduplicated repair brief to the original owner Topic. After one failed repair, missing evidence, verifier disagreement or contract drift, escalate instead of retrying.

## Acceptance and release boundary

Use LobeHub's installed `acceptance` skill to publish observable evidence into immutable rounds. Program gates remain linked preconditions. A new repair produces a new round; never rewrite old evidence.

Call `decide_release_readiness` for policy advice. It cannot authorize or perform push, merge, deploy, publish, purchase, quota reset, destructive migration or any other external action. Direction, aesthetics, irreversible effects and final acceptance remain human decisions.
