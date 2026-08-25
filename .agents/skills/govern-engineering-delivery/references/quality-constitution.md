# Quality constitution

The delivery is evaluated against the frozen work contract and only the dimensions selected by risk and change surface.

1. Outcome fidelity: every agreed user/operator outcome is observable; non-goals and prohibited behaviors remain absent.
2. Architecture and compatibility: module, API, data and dependency invariants survive the change.
3. Test quality: tests use meaningful oracles and would fail for realistic regressions; green tests alone are not Acceptance.
4. Security and privacy: claims identify a trust boundary, attacker-controlled path and concrete evidence.
5. Supply chain: dependency, lockfile, privilege and delivery-chain changes are accounted for.
6. Experience: the real UI, CLI or API flow, including failure and accessibility behavior, is exercised.
7. Performance: declared latency, throughput and resource budgets are measured against a baseline.
8. Observability: success, failure, cost and recovery can be understood without reading source code.
9. Operations: rollout, migration, interruption and recovery are safe and rehearsable.
10. Release accountability: compatibility, rollback and human decision boundaries are explicit.

Deterministic evidence is preferred wherever an executable oracle exists. AI judgment is reserved for semantics, omissions, adversarial counterexamples and experience that a program cannot decide reliably.

The change does not need every dimension on every run. Low-risk documentation may need only outcome evidence. Authentication, permissions, billing, data migration, dependencies or infrastructure expand the required dimensions and human checkpoints.
