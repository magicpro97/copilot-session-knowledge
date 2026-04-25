# browse-ui Design Phase Artifacts

This folder is the **design phase output** for the `browse-ui/` rebuild
(Next.js 15 + shadcn/ui + Tailwind v4). All implementation in Pha 5+ MUST be
traceable back to a section in these docs.

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

**Design phase: CLEAN.** Ready for Pha 5 (API extraction + Python envelope
updates) and Pha 6 (`browse-ui/` scaffold).

Process used: 4 parallel design agents (Opus-4.6) → cross-review (Opus-4.6
rubber-duck) → fix synthesis (Opus-4.6) → reader test (Sonnet-4.6) → medium
fix (Sonnet-4.6). Orchestrator did not write any design content directly.
