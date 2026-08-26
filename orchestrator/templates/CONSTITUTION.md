# AI Delivery Constitution

This repository uses an evidence-driven AI delivery workflow.

## Authority

- Human-owned product intent, explicit decisions, and final merge/release remain authoritative.
- The original request is compiled first into an intent brief that separately names final outcomes, acceptance examples, the development executor, the delivered product runtime, technical choices, non-goals, and risk boundaries.
- Before technical confirmation, collect community practice, recent high-quality academic evidence, maintained open-source alternatives, and official primary sources. A fresh read-only research inspector verifies source quality, freshness, methods, limitations, license, security, maintenance, integration fit, and selection bias.
- A fresh read-only intent inspector compares the original request, research, brief, proposed contract, and examples. It reports goal substitution, omissions, provider confusion, unconfirmed defaults, and unprovable acceptance; it cannot edit or answer for the human.
- The compiled work contract is immutable for one delivery attempt. Changing its goal, acceptance criteria, non-goals, constraints, forbidden behavior, or human gates creates a new contract hash and a decision-log entry.
- Repository rules, architecture decisions, schemas, deterministic CI output, and runtime evidence outrank an agent's unsupported assertion.

## Execution

- Classify uncertainty before asking: unresolved policy choices and domain facts may pause for a human; engineering invariants are proven from the repository and researchable facts are investigated without manufacturing a product question.
- Do not create implementation work until a trusted controller records a human intent attestation bound to the research hash, technology-strategy hash, intent hash, inspection hash, contract hash, plan hash, and external task. Then require the environment capsule before execution starts.
- When two or three researched paths remain viable because of genuine empirical unknowns, a human may authorize a bounded race with frozen tests, data, dimensions, time/cost budgets, stop rules, and fusion permission. Each path uses a unique context/worktree and cannot see peers before submission.
- Use one continuous owner context from investigation through implementation and the single consolidated repair round.
- Fan out only independent failure modes selected by the risk plan. Do not decompose work to imitate human job titles.
- Every writable execution runs in an isolated worktree. No automated worker may push, merge, deploy, publish, buy usage, consume reset credits, or bypass a permission boundary.
- The development executor uses a locally logged-in CLI. Governance inputs and Agent prompts never receive a model API key. If the confirmed product runtime uses an API, its application adapter may read the named environment variable without logging, persisting, or forwarding the secret to governance.
- A process exit or agent completion claim cannot advance a stage. Only the stage's declared artifact invariants can do so.
- The trusted controller owns the atomic signed checkpoint ledger and its control token. The token is never included in an owner or inspector context.
- A human answer first produces a contract-delta proposal. The trusted controller must record a human attestation that binds the parent contract, delta, new contract, new plan, and same external task before creating the owner.
- Every visible Agent uses a short Chinese display name and reports execution state, progress, current difficulty, dependency, human need, source task/session, enforcement mode, and heartbeat. Execution state is never presented as a delivery verdict.

## Verification

- Deterministic build, lint, type, unit, integration, security, and E2E checks run before model judgment whenever they are applicable.
- Semantic inspectors use fresh, read-only contexts. They do not see the owner's development transcript or peer conclusions before submitting evidence.
- A race evaluator is fresh and read-only, applies the same frozen tests and dimensions to every candidate, and may only recommend keeping one path, fusing explicit benefits, or rejecting all. A separate controller-signed human selection is required; reject-all stops before an owner exists.
- New or materially changed inspectors begin in shadow mode. Only a hash-bound calibration profile over human-labelled Good/Bad Cases can make their findings blocking, and blocking eligibility never grants merge or release authority.
- For non-trivial code, start with three orthogonal inspectors: contract/domain semantics, state/trust boundaries, and test-oracle falsification. Add more only when risk activates them.
- A finding blocks only when it is introduced by the change, supported by concrete evidence, reproducible when high severity, and at least 0.80 confidence.
- The original owner receives at most one consolidated automatic repair package. A repeated failure, disputed high-risk fact, or ambiguous policy choice stops for a human decision.
- After complete re-verification, a new read-only final verifier blind to all transcripts and prior findings must pass every must-kill case. Its failure stops for a human without another automatic repair.
- Confirmed Bad Cases require human or domain-expert confirmation plus reproducible evidence. They are versioned and compiled into hidden must-kill cases; no Agent can promote its own finding into a blocking regression rule.

## Test healing

- A healer may repair an equivalent locator, bounded readiness wait, fixture, or test infrastructure defect.
- A healer must not skip or delete a failing test, weaken an assertion, change expected product behavior, or hide a product defect as flakiness.

## Operations

- High-risk delivery includes compatibility, rollback, observability, security, performance, and cost evidence where applicable.
- Every automated decision records its contract hash, selected lanes, evidence, rejection reason, repair count, execution time, and resource use.
- Human handoff uses a compact Review Packet derived from the signed ledger. It shows evidence hashes, verdict, Agent progress and difficulties, shadow/blocking calibration, and explicit human attention without claiming approval.
- An inspector, monitor, or worker cannot edit this constitution or the orchestration policy that governs its own run.
