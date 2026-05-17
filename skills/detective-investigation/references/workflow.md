# Detective Investigation Workflow

## 1. Intake

Create a situation map:

| Field | Question |
| --- | --- |
| Who | Who is affected? |
| What | What is wrong? What should happen instead? |
| Where | Which screen, API, service, job, table, or integration? |
| When | When did it start? Which release/deploy/config window? |
| Impact | How severe and how frequent? |

## 2. RED evidence

Capture proof of failure before deep analysis:

- failing test,
- reproduction steps,
- screenshot or video,
- log or trace,
- API response,
- database/config snapshot.

The key is a reusable assertion: the same check should later verify GREEN.

## 3. Evidence collection

For each item, record source, timestamp, tier, direction, artifact path, and citation. Evidence can support, contradict, or merely contextualize a hypothesis.

## 4. Timeline

Order observable events before claiming causality. Include reports, deploys, commits, migrations, config changes, monitoring events, and reproductions.

## 5. Hypothesis graph

Create nodes for symptoms, observations, hypotheses, findings, root causes, contradictions, unknowns, fixes, and verification. Create typed edges such as `supports`, `contradicts`, `causes`, `fixed_by`, and `verified_by`.

## 6. Challenge and prune

Actively look for:

- strongest evidence against the leading hypothesis,
- alternative explanations,
- hidden confounders,
- unsupported claims,
- circular reasoning,
- temporal violations,
- similar paths where the bug should also appear.

## 7. Root-cause proof

Promote only when actionability and counterfactual clarity are strong. If fixing the proposed root cause would not have prevented the failure, it is probably a mitigation or symptom fix.

## 8. GREEN verification

Use the same assertion from RED. If the fix is not implemented yet, mark verification as pending and list exactly what evidence is required.

## 9. Report

Generate a report from the graph data. Keep open questions visible so future investigators can continue from the current board.

