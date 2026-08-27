# Fresh-driver bootstrap

This file is a project-local entry point. It does not contain conversation
history and must never import product facts from another repository.

## Mandatory order

1. Bind to the exact Git repository selected by the human or current task.
2. Run the governance repository's read-only verifier:

   ```text
   python3 <governance-repository>/run.py governance takeover --target <this-repository>
   ```

3. Continue only when the result says `ready_for_takeover: true`.
4. Read exactly `required_read_order` from that result before proposing work.
5. Treat `canonical_sources` as current. Treat `historical_sources` as evidence
   only; never resume from a historical plan or report.
6. Preserve every pre-existing working-tree change. The takeover report grants
   no permission to push, merge, deploy, publish, spend money, contact people,
   use secrets, or expand scope.

## Project isolation

- The selected repository's `project_id` is the product-context boundary.
- Project requirements, decisions, examples, prompts, evidence, Good/Bad Cases,
  runtime choices, and user data never cross that boundary by default.
- Global learning may contain only generic governance policy, anonymized failure
  patterns, and reusable evaluation methods. It must not contain a project's
  domain facts, customer material, expected outputs, credentials, or active
  decisions.
- If the current directory is not one uniquely selected project, stop and ask
  the human to identify the target. Never guess from recency or a sibling repo.

## Honest recovery

The fresh driver may report or diagnose a blocked packet, but it must not repair
the continuity artifacts and then approve its own takeover in the same step.
Conflicting hashes, Git identity, current-state assertions, or project identity
require a reviewable update and a subsequent clean verification.
