# HTML Evidence Board

## Recommended architecture

Use a reusable template that reads investigation data and renders:

- graph canvas,
- node detail panel,
- timeline,
- evidence table,
- conclusion and unknowns.

The template can use Cytoscape.js for interaction. For fast static output, render Mermaid or D2 from the same graph data.

## Layout

```text
Header: title, status, confidence, search, filters, export
Graph canvas | Detail panel
Timeline strip
Evidence table
Conclusion and unknowns
```

## Interactions

| Interaction | Behavior |
| --- | --- |
| Search | Filter nodes, edges, and evidence |
| Type filter | Show only symptoms, hypotheses, evidence, root causes, unknowns, etc. |
| Status filter | Show open, confirmed, refuted, escalated, verified |
| Node click | Detail panel with evidence and citations |
| Edge click | Explain the relationship and evidence |
| Focus mode | Show selected node and nearby graph |
| Path highlight | Highlight symptom-to-root-cause-to-fix path |
| Export | JSON, Markdown, SVG/PNG, printable HTML |

## Visual encoding

| Node | Suggested style |
| --- | --- |
| Evidence | blue circle |
| Hypothesis | purple diamond |
| Finding | amber rounded rectangle |
| Root cause | red star or triangle |
| Unknown | gray hexagon |
| Contradiction | warning icon |
| Fix | green square |
| Verification | teal double circle |

Use labels and shapes in addition to color.

## Validation warnings

The report generator should warn when:

- a root cause lacks strong evidence,
- a hypothesis has no supporting evidence,
- a contradiction is unresolved,
- a high-impact unknown has no next action,
- a fix has no verification node,
- a claim has no citation or artifact path.

