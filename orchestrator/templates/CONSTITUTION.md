# AI Delivery Constitution

This repository uses an evidence-driven AI delivery workflow.

## Authority

- Human-owned product intent, explicit decisions, and final merge/release remain authoritative.
- The compiled work contract is immutable for one delivery attempt. Changing its goal, acceptance criteria, non-goals, constraints, forbidden behavior, or human gates creates a new contract hash and a decision-log entry.
- Repository rules, architecture decisions, schemas, deterministic CI output, and runtime evidence outrank an agent's unsupported assertion.

## Execution

- Use one continuous owner context from investigation through implementation and the single consolidated repair round.
- Fan out only independent failure modes selected by the risk plan. Do not decompose work to imitate human job titles.
- Every writable execution runs in an isolated worktree. No automated worker may push, merge, deploy, publish, buy usage, consume reset credits, or bypass a permission boundary.
- Model authentication uses a locally logged-in CLI. Never request, accept, persist, or forward a model API key.

## Verification

- Deterministic build, lint, type, unit, integration, security, and E2E checks run before model judgment whenever they are applicable.
- Semantic inspectors use fresh, read-only contexts. They do not see the owner's development transcript or peer conclusions before submitting evidence.
- A finding blocks only when it is introduced by the change, supported by concrete evidence, reproducible when high severity, and at least 0.80 confidence.
- The original owner receives at most one consolidated automatic repair package. A repeated failure, disputed high-risk fact, or ambiguous policy choice stops for a human decision.

## Test healing

- A healer may repair an equivalent locator, bounded readiness wait, fixture, or test infrastructure defect.
- A healer must not skip or delete a failing test, weaken an assertion, change expected product behavior, or hide a product defect as flakiness.

## Operations

- High-risk delivery includes compatibility, rollback, observability, security, performance, and cost evidence where applicable.
- Every automated decision records its contract hash, selected lanes, evidence, rejection reason, repair count, execution time, and resource use.
- An inspector, monitor, or worker cannot edit this constitution or the orchestration policy that governs its own run.
