<!-- Root instruction surface: see ../AGENTS.md for repo-wide agent rules. -->

<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

## Lint scope

`pnpm lint` covers `src/` only and is the blocking CI gate.
`pnpm lint:all` is advisory and additionally covers `e2e/` and `scripts/`; CI now runs it as an advisory step (continue-on-error: true) in the browse-ui job (issue #459).
Run `pnpm lint:all` locally before touching test or build scripts outside `src/`.
