---
name: ai-delivery-governance
description: Compile a work contract, select risk-based isolated verification lanes, or adjudicate evidence for a non-trivial software delivery. Use for autonomous or multi-agent development intended to pass quality gates; do not use for an ordinary one-off edit that the user wants handled directly.
---

# AI Delivery Governance

Preserve one continuous owner context for investigation, implementation, and the single consolidated repair. Add fresh contexts only for independent failure modes selected by risk; do not imitate human job titles.

The versioned work contract is the source of truth for intent. Deterministic CI is the source of truth for machine-checkable behavior. Inspectors are read-only and submit evidence; they never fix product code or approve their own work. Humans retain ambiguous product choices, policy, taste, irreversible/external actions, and final merge or release.

Use the mode that matches the current need:

- For intake, clarification, or preventing requirement drift, read [references/contract.md](references/contract.md).
- For deciding single-owner versus independent inspectors, or preparing inspector contexts, read [references/verification.md](references/verification.md).
- For filtering findings, producing one repair package, or deciding whether to stop for a human, read [references/adjudication.md](references/adjudication.md).

When this repository's governance CLI is available, prefer its deterministic contract, routing, and adjudication output over recreating policy in prose:

```text
python3 run.py governance compile --input contract-source.json
python3 run.py governance route --input compiled-contract.json
python3 run.py governance contexts --input contract-and-plan.json
python3 run.py governance handoff --input contract-and-plan.json
python3 run.py governance adjudicate --input evidence.json
```

Never request a model API key, bypass a permission boundary, mutate an external system, push, merge, deploy, publish, purchase usage, or consume reset credits merely because this skill is active.
