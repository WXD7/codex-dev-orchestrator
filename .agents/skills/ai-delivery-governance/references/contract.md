# Work contract

Compile technology research before the intent brief and work contract. It must cover community experience, recent high-quality academic evidence, at least two maintained open-source candidates, and official primary sources. A fresh read-only research inspector checks freshness, methods, limitations, maintenance, license, security, integration fit, and selection bias.

The intent brief must separately show the original request, final outcomes, concrete input/output examples, development executor, delivered product runtime provider/model/authentication boundary, high-impact technical choices and rationale, non-goals, and risk boundaries.

A fresh read-only intent inspector must compare the original request, research, brief, proposed contract, and examples. Require explicit coverage for every outcome, example, technical choice, research recommendation, human technology strategy, development executor, and product runtime. Block goal substitution, omission, provider confusion, unconfirmed defaults, and unprovable acceptance. The inspector asks the human; it does not edit or answer for them.

After intent inspection passes, compile the contract. The contract must separate:

- goal and observable outcomes;
- users and operating scenario;
- acceptance criteria stated independently of implementation;
- non-goals and forbidden behavior;
- technical, legal, privacy, cost, and compatibility constraints;
- deterministic checks expected to prove completion;
- explicit risk flags;
- decisions reserved for a human.

Do not silently fill a missing acceptance oracle or a high-risk human decision. Return targeted clarification questions and keep the contract in `needs_clarification`.

Do not create implementation until a trusted controller records a human HMAC attestation bound to the research hash, technology-strategy hash, intent hash, inspection hash, contract hash, plan hash, and same external task. A PASS result is not a human attestation.

If two or three researched paths remain viable because of real unknowns, let the human authorize a bounded race with identical data, tests, dimensions, time/cost budget, stop rules, and fusion permission. Candidates use unique contexts/worktrees and remain mutually blind. A fresh read-only evaluator recommends keep/fuse/reject-all; only a controller-signed human decision chooses the path.

Treat goal, outcomes, acceptance criteria, non-goals, constraints, forbidden behavior, and human gates as immutable for one attempt. A material change produces a new contract hash and a decision-log entry. Incidental implementation detail belongs in the plan, not the intent contract.

Keyword-inferred risk is only a routing signal. Confirm it against the repository and user context before treating it as fact.
