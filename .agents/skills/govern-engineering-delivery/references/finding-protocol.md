# Finding protocol

Each finding should contain:

```json
{
  "dimension": "security",
  "severity": "high",
  "confidence": 92,
  "summary": "One falsifiable defect statement",
  "location": "path:line or observable surface",
  "introduced_by_change": true,
  "reproducible": true,
  "evidence": ["command/result, trace, DOM state, screenshot, or data-flow fact"],
  "counterexample": "Minimal behavior that violates the frozen outcome",
  "suggested_verification": "How to prove the repair, not a demanded implementation"
}
```

Report a finding only when confidence is at least 80. High and critical findings need a reproduction or concrete evidence. Do not report style preferences, deterministic lint/type noise, speculative hardening without an exploit path, pre-existing defects unrelated to the change, or duplicates from another lane.

Aggregation deduplicates by dimension, location and summary. The repair brief contains all verified blockers at once and goes to the original owner Topic. A verifier never edits the delivery itself.
