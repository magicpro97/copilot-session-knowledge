# browse-ui Design Phase Artifacts

This folder is the **design-phase archive** for the `browse-ui/` rebuild
(Next.js + shadcn/ui + Tailwind v4). Shipped implementation in Pha 5–10 is
traceable back to these docs.

## Read order

1. **[01-system-architecture.md](01-system-architecture.md)** — repo layout,
   API contract, build/serve, strangler routing, auth, state, CI, migration,
   perf budget, risk register.
2. **[02-ux-information-architecture.md](02-ux-information-architecture.md)** —
   17→8 routes, page-by-page wireframes, components (what / why / how),
   density modes, keyboard shortcuts.
3. **[03-visual-design.md](03-visual-design.md)** — color tokens (HSL,
   `#5E6AD2` primary), typography (Geist), spacing scale, charts (Recharts),
   component visual specs.
4. **[04-standards-tooling.md](04-standards-tooling.md)** — auto-update
   coverage, hook rules (`block_edit_dist`, `nextjs_typecheck`,
   `pnpm_lockfile_guard`, `block_unsafe_html`), skill, lint/format pipeline,
   pre-flight checklist.

## Audit trail

- **[00-cross-review.md](00-cross-review.md)** — Opus-4.6 skeptical review of
  the 4 design docs. 3 BLOCKERs + 8 MAJORs + 4 MINORs found and resolved
  (see "Fix log" at the end).
- **[00-reader-test.md](00-reader-test.md)** — fresh-implementer reader test
  (12 questions). 9 HIGH, 3 MEDIUM (resolved). 0 BLOCKERs. Design verified
  implementable without follow-up clarification.

## Status

**Design phase: COMPLETE and implemented.** The planned work is now shipped
through Pha 10, including runtime acceptance, deterministic Playwright lanes,
and final shell polish.

This directory remains the canonical rationale/audit trail for implementation
decisions and regression checks.
