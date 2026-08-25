# Verification lanes

A lane is an isolated evidence question, not a human department or permanent persona.

- Reuse the owner Topic for implementation and repair so investigation context is not discarded.
- Open a fresh Topic for adversarial falsification so the reviewer does not inherit the author's conclusions.
- Split lanes only when their evidence inputs and failure modes are materially independent: outcome, architecture/performance, test oracle, security/supply chain, experience, or operations/release.
- All verifier lanes are read-only. They may inspect diffs, run non-mutating checks and collect evidence. They may not edit files, commits, tasks, configuration or Acceptance history.
- Give each lane only the frozen contract, relevant diff/files, repository rules and deterministic gate evidence. Do not pass the owner's persuasive narrative as ground truth.
- Wait for every blocking lane selected by `build_verification_plan`. Missing lane output is missing evidence, not a pass.

Create a lane only when it has marginal value over the continuous owner. If it cannot name a distinct evidence question or contamination risk, keep the work in the owner Topic.
