# Evidence Model

## Canonical structure

```json
{
  "schemaVersion": "1.0",
  "case": {
    "id": "case-id",
    "title": "Short title",
    "status": "open | red_captured | root_cause_confirmed | fixed | verified | escalated",
    "summary": "Current conclusion"
  },
  "situation": {
    "who": ["affected users"],
    "what": "symptom",
    "where": ["systems or files"],
    "when": "time window",
    "impact": "severity, recurrence, affected users or data",
    "expected": "expected behavior",
    "actual": "actual behavior"
  },
  "nodes": [],
  "edges": [],
  "evidence": [],
  "artifacts": [],
  "conclusions": [],
  "openQuestions": []
}
```

## Node types

| Type | Meaning |
| --- | --- |
| `symptom` | User-visible or system-visible failure |
| `observation` | Directly observed fact |
| `hypothesis` | Candidate explanation |
| `finding` | Hypothesis backed by strong evidence |
| `root_cause` | Finding that passes counterfactual and actionability checks |
| `contradiction` | Evidence that conflicts with another claim |
| `unknown` | Missing information or hidden node |
| `fix` | Proposed or implemented resolution |
| `verification` | GREEN proof |

## Edge types

| Type | Meaning |
| --- | --- |
| `causes` | Source causes or explains target |
| `supports` | Evidence supports a claim |
| `contradicts` | Evidence conflicts with a claim |
| `depends_on` | Claim requires another condition |
| `same_pattern_as` | Similar risk or lateral-check relation |
| `fixed_by` | Fix addresses root cause |
| `verified_by` | Verification proves fix |

## Depth criteria

Root cause should pass:

1. Actionability: there is a concrete change or accepted risk.
2. Counterfactual clarity: if the change existed earlier, the failure would not happen.
3. System boundary: fix can be enforced at a stable boundary such as schema, validation, tests, CI, interface, or ownership.
4. Diminishing returns: asking another "why" would not change the action.

## Unknown nodes

Use unknown nodes when a missing fact could change the conclusion. Give each unknown:

- question type,
- required evidence,
- impact on conclusion,
- suggested next action.

## Conclusion objects

```json
{
  "id": "conc-001",
  "claim": "Root cause is X because evidence ev-001 and ev-002 support it.",
  "status": "preliminary | confirmed | disputed",
  "supportingNodeIds": ["root-001"],
  "supportingEvidenceIds": ["ev-001", "ev-002"],
  "confidence": 0.9,
  "counterfactual": "If the proposed fix existed before, the failure would not occur."
}
```
