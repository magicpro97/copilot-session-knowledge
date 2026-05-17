---
name: detective-investigation
description: Create professional detective-style investigations and reusable evidence-board reports for bugs, incidents, regressions, unclear failures, postmortems, or root-cause analysis in any software project. Use when the user asks to investigate root cause, build an evidence board, reason from clues like a detective, identify hidden unknowns, compare working vs broken behavior, prove or refute hypotheses, generate an interactive HTML report, or turn scattered logs/screenshots/issues/code findings into an auditable investigation graph. It is project-agnostic and designed to be copied from ~/.copilot/tools/skills into any repo.
---

# Detective Investigation

Use this skill to investigate bugs and incidents as an evidence graph instead of a loose prose narrative. The output should be reusable: new evidence can be added later and the report can be regenerated without rebuilding the whole board.

## When to use

Use this skill when a project needs a rigorous, evidence-backed investigation workflow:

- root-cause analysis for bugs, incidents, regressions, or outages,
- "detective board" reasoning from scattered clues,
- interactive HTML evidence board or graph report,
- comparison of working vs broken behavior,
- hidden unknowns, contradictions, or alternative hypotheses,
- postmortem preparation where claims need citations and artifacts.

## Read only what you need

| Need | Read |
| --- | --- |
| End-to-end investigation stages | `references/workflow.md` |
| Evidence, hypothesis, and root-cause model | `references/evidence-model.md` |
| Interactive HTML board guidance | `references/html-board.md` |

## Core idea

Capture investigation facts in a canonical JSON/YAML structure, then generate Markdown or HTML from that data. This keeps the workflow portable across projects and avoids hand-coding a new report for every bug.

## Workflow

1. **Frame the case**: define symptom, expected behavior, actual behavior, affected users, environment, and time window.
2. **Capture RED evidence**: obtain a reproducible failure or direct observation before proposing a root cause.
3. **Collect and tier evidence**: classify each item as physical, observational, inferential, or testimonial.
4. **Reconstruct timeline**: order reports, logs, deploys, commits, config changes, traces, and reproductions.
5. **Build hypothesis graph**: connect symptoms, evidence, hypotheses, contradictions, unknowns, root causes, fixes, and verification.
6. **Challenge the graph**: look for contradictions, temporal violations, unsupported claims, and hidden confounders.
7. **Promote root cause carefully**: require strong evidence and counterfactual clarity.
8. **Verify GREEN**: prove the same RED assertion passes after the fix or mitigation.
9. **Generate report**: produce an evidence-board HTML/Markdown report with citations and open questions.

## Required report sections

1. Executive summary
2. Scope and symptom
3. RED evidence
4. Timeline
5. Evidence graph
6. Hypotheses considered
7. Contradictions and unknowns
8. Root-cause proof
9. Fix or mitigation
10. GREEN evidence
11. Lateral or similar-pattern check
12. Remaining questions and next actions
13. Citations and artifacts

## Evidence tiers

| Tier | Examples | Weight |
| --- | --- | --- |
| Physical | logs, traces, database rows, API responses, failing tests | 1.00 |
| Observational | screenshots, videos, direct reproduction notes | 0.75 |
| Inferential | code reading, diffs, reasoned mapper/data-flow analysis | 0.50 |
| Testimonial | remembered behavior, second-hand reports | 0.25 |

## Promotion rules

| Promotion | Requirement |
| --- | --- |
| Hypothesis -> Finding | At least one physical or observational supporting evidence item |
| Finding -> Root cause | Evidence support, no unresolved high-impact contradiction, and counterfactual clarity |
| Root cause -> Resolution | Concrete fix, mitigation, or accepted risk |
| Fix -> Verified | Same RED assertion passes after the change |

## Output modes

| User need | Output |
| --- | --- |
| Fast investigation report | Markdown with Mermaid/D2 graph |
| Interactive evidence board | Single-file HTML with Cytoscape.js-style graph data |
| Handoff to another agent/team | `investigation.json` plus artifacts and citations |
| Follow-up investigation | Update nodes/edges/evidence and regenerate report |

## Stop conditions

Stop and report uncertainty when:

- expected and actual behavior are not clear,
- RED evidence is missing,
- root cause has only weak evidence,
- unresolved contradiction could overturn the conclusion,
- an unknown node has high impact and no required evidence yet,
- proposed fix fails the counterfactual question: "If this existed before, would the incident not have happened?"

<example>
User: We have a regression where billing exports show zero totals after last release. Investigate it like a detective and make an evidence board.

Assistant behavior: Capture RED evidence, classify logs/screenshots/code findings by evidence tier, build a hypothesis graph, mark contradictions and unknowns, then produce an auditable report with root-cause proof and GREEN verification plan.
</example>

<example>
User: Turn these scattered screenshots, logs, and PR notes into an interactive HTML bug board with root cause and next actions.

Assistant behavior: Normalize the material into case/situation/nodes/edges/evidence, recommend a reusable HTML board layout, keep citations attached to claims, and avoid one-off hand-written diagrams.
</example>
