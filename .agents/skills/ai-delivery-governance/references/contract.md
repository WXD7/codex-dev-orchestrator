# Work contract

Compile intent before implementation. The contract must separate:

- goal and observable outcomes;
- users and operating scenario;
- acceptance criteria stated independently of implementation;
- non-goals and forbidden behavior;
- technical, legal, privacy, cost, and compatibility constraints;
- deterministic checks expected to prove completion;
- explicit risk flags;
- decisions reserved for a human.

Do not silently fill a missing acceptance oracle or a high-risk human decision. Return targeted clarification questions and keep the contract in `needs_clarification`.

Treat goal, outcomes, acceptance criteria, non-goals, constraints, forbidden behavior, and human gates as immutable for one attempt. A material change produces a new contract hash and a decision-log entry. Incidental implementation detail belongs in the plan, not the intent contract.

Keyword-inferred risk is only a routing signal. Confirm it against the repository and user context before treating it as fact.
