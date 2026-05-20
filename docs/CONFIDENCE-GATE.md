# Decision Confidence Gate

The decision confidence gate prevents agents from turning an ambiguous plan into code,
deletions, PRs, or merges. A plan with confidence below `1.0` is treated as an open research
gate, not as permission to continue.

## Contract

Any router/conductor can publish the current decision state to one of these files:

| Scope | Path |
|---|---|
| Project conductor | `.github/conductor/last-plan.json` |
| Project-local generic marker | `.copilot/confidence-gate.json` |
| User-global marker | `~/.copilot/markers/confidence-gate.json` |

An open gate uses this shape:

```json
{
  "research_gate": {
    "required": true,
    "confidence_score": 0.6,
    "required_confidence": 1.0,
    "sub_research_tasks": []
  }
}
```

When the gate is open, `hooks/rules/confidence_gate.py` blocks writes and closeout-style
operations. Read-only commands and research commands remain allowed so the agent can gather
evidence.

## Required response to confidence `< 1.0`

1. Stop implementation, deletion, merge, PR, and routing decisions for the uncertain scope.
2. Split the ambiguity into atomic research questions.
3. Dispatch independent research/validation agents on the strongest available model
   (`claude-opus-4.7` when available; otherwise newest opus-class model).
4. Record evidence, rejected alternatives, and remaining gaps.
5. Rerun the conductor/router. Continue only when the plan has no `research_gate`, meaning the
   synthesized confidence is `1.0`.

## Explicit override

An override is intentionally auditable. Add `"override": true` or
`"confidence_gate_override": true` only when the user explicitly accepts the risk and the
rationale is recorded in the session or PR. Do not use overrides to bypass missing research.
