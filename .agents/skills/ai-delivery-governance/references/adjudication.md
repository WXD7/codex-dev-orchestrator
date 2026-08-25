# Evidence adjudication

Require each semantic finding to include a concise title, lane, location when applicable, severity, confidence, evidence, reproduction, and whether the current change introduced it.

Reject or demote:

- confidence below 0.80;
- pre-existing issues outside the contract scope;
- stylistic opinions without an explicit repository rule;
- issues already enforced more reliably by deterministic CI;
- high/critical claims without a reproduction or equivalent concrete artifact;
- duplicates of the same root cause and location.

Required deterministic failures block regardless of model confidence. Reproducible high/critical semantic findings block. Lower-severity accepted findings are warnings unless the work contract explicitly elevates them.

Aggregate all blockers into one repair package for the original owner context. Allow at most one automatic repair round, then rerun the complete deterministic and selected semantic plan. Repeated failure, disputed high-risk evidence, contract ambiguity, or a required external/irreversible action stops for a human.

The final successful state is `ready_for_human_merge`, not self-approval. Record accepted, rejected, and deduplicated counts so inspector precision and marginal value can be measured over time.
