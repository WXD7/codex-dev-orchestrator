# Workspace project router

This directory is a router, not a product project and not a product-memory
store.

1. Bind each product task to exactly one Git repository selected by the human
   or uniquely established by the current working directory.
2. If several repositories are plausible, stop and ask the human to identify
   the target. Never choose from conversation recency, UI history, sibling
   directory order, or remembered product details.
3. Follow the selected repository's project-local `AGENTS.md` and
   `.ai-delivery/DRIVER_BOOTSTRAP.md`. Run its read-only takeover verification
   before reading product-specific sources or changing files.
4. Never search a sibling repository to fill a missing requirement, decision,
   prompt, example, expected output, case, credential, evidence, or current
   state.
5. Global learning is limited to generic governance policy, anonymized failure
   patterns, and reusable evaluation methods. Product-specific learning stays
   in the selected repository.
6. Driver and workflow self-improvements belong in the governance repository;
   product behavior belongs in the bound product repository.

A successful takeover does not grant permission to push, merge, deploy,
publish, spend money, read secret values, contact people, or perform another
external model call.
