# Verification routing

Start with required deterministic checks. A required check that cannot run is not a pass.

Keep low-risk documentation, formatting, or tests-only work in one owner context unless evidence shows another failure mode. For non-trivial code, choose only applicable lanes:

- requirement conformance: acceptance criteria and forbidden behavior;
- code and architecture: explicit repository rules, invariants, compatibility, blast radius;
- test quality: weak assertions, false mocks, missing boundaries, mutation resistance;
- security: attacker-controlled input, permissions, secrets, dependencies, exploitability;
- data compatibility: schema/API evolution, tenant boundaries, reversible migration and rollback;
- E2E and UX: real user flow, accessibility, visual evidence;
- reliability and cost: latency, concurrency, failure recovery, resource and agent budget;
- adversarial falsification: high-risk abuse, boundary, recovery, and conflicting-state scenarios.

Each semantic lane gets a fresh, read-only context containing the contract hash, Diff, lane purpose, relevant repository rules, deterministic evidence, and only the additional artifacts it needs. Exclude the owner's development transcript and peer conclusions until all inspectors submit.

Do not run all lanes by default. Record why each lane was enabled, its time/token cost, and whether it contributed a unique confirmed defect.

A test healer may repair an equivalent locator, bounded readiness wait, fixture, or test infrastructure defect. It must not skip tests, weaken assertions, change expected behavior, edit product code, or relabel a product defect as flakiness.
