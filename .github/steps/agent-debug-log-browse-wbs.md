# STEPS: WBS-101 / WBS-102 Agent Debug Log Browse Contract

**Task:** Define the Agent Debug Log browse contract (`BrowseDebugEntry`, `DebugLogResponse`),
deterministic synthetic JSONL fixtures, and security/redaction placeholder for WBS-102.
**Scope:**
- `.github/steps/agent-debug-log-browse-wbs.md` (this file)
- `docs/DEBUG-LOG-CONTRACT.md`
- `tests/fixtures/debug-log/**`

**Source issue:** <https://github.com/magicpro97/copilot-session-knowledge/issues/426>
**Blocks:** WBS-103 through WBS-110 (backend routes, UI components, Zod schemas, redaction engine)
**Parallel with:** WBS-102 (security and redaction policy)

---

## Step-plan review

### Accepted steps

1. Create this reviewed task-step scaffold (WBS-101/WBS-102 mapping, dependency order, evidence gates).
2. Create `docs/DEBUG-LOG-CONTRACT.md` with full `BrowseDebugEntry` / `DebugLogResponse` definition,
   event taxonomy, synthetic span-id rule, timestamp/duration fallback, preview/truncation,
   VS Code / OTel / OTLP mapping, non-goals, and a reserved WBS-102 redaction placeholder.
3. Create deterministic synthetic JSONL fixtures under `tests/fixtures/debug-log/` plus a
   `README.md` declaring synthetic provenance and forbidding real-session contributions.
4. Run and record evidence: JSONL parse, doc coverage grep, anonymization grep, `python
   test_security.py`, and any existing lightweight lint/doc checks.

### Edited steps

- The issue mentioned using real local session files as fixture sources. **Edited:** real local data
  is a shape reference only; all committed fixtures are deterministic synthetic JSONL with neutral
  placeholder content (`fixture-session-001`, etc.).

### Rejected steps

- Backend route implementation (`browse/routes/debug_log.py`) — out of scope, belongs to WBS-103.
- Frontend UI implementation (`browse-ui/src/...`) — out of scope, belongs to WBS-104/WBS-105.
- TypeScript/Zod schema implementation — out of scope, belongs to WBS-106.
- Redaction engine implementation — out of scope, belongs to WBS-102.
- Any capture, anonymization, or commit of real Copilot CLI / operator session data.

---

## Dependency order

```
WBS-101 (contract + fixtures) ──┐
WBS-102 (redaction policy)  ────┼──> WBS-103 (backend GET route)
                                │    WBS-104 (UI debug log panel)
                                │    WBS-105 (UI event row renderer)
                                │    WBS-106 (TS/Zod schemas)
                                │    WBS-107 (SSE streaming)
                                └──> WBS-108..WBS-110 (integration/perf/CI)
```

---

## Confidence and decisions

| Decision | Confidence | Rationale |
|---|---|---|
| Fixtures are deterministic synthetic JSONL only | 1.0 | Opus research: no real session data in repo |
| `span_id = sha1(f"{source}:{idx}:{seq}")[:16]` | 1.0 | Derived from OTel 16-hex requirement; unique per event |
| `timestamp = null` when absent; never synthesize | 1.0 | Prevents false ordering in UI; matches OTel nullable semantics |
| `duration_ms = null` when no paired completion | 1.0 | Sentinel `0` would break "unknown vs zero-duration" distinction |
| 200-char message preview; full text excluded | 1.0 | Matches existing `_PREVIEW_LEN = 200` in `browse/routes/timeline.py` |
| `chatSessions`/`transcripts` are NOT sources | 1.0 | They contain user content; debug log is agent telemetry only |
| `schema_version = "1"` for initial contract | 1.0 | Allows forward-compat bump without breaking consumers |

---

## Evidence gates (required before WBS-103 can proceed)

| Gate | Command | Expected |
|---|---|---|
| JSONL parses cleanly | `python -c "import json; [json.loads(l) for l in open('tests/fixtures/debug-log/debug-log-sample.jsonl') if l.strip()]"` | No exception |
| Doc coverage: BrowseDebugEntry | `grep -c "BrowseDebugEntry" docs/DEBUG-LOG-CONTRACT.md` | >= 1 |
| Doc coverage: DebugLogResponse | `grep -c "DebugLogResponse" docs/DEBUG-LOG-CONTRACT.md` | >= 1 |
| Doc coverage: event taxonomy | `grep -c "session_start" docs/DEBUG-LOG-CONTRACT.md` | >= 1 |
| No real session paths in fixtures | `grep -rn "\.copilot\|USERPROFILE\|/home/" tests/fixtures/debug-log/` | No matches |
| No tokens/keys in fixtures | `grep -rn "ghp_\|sk-\|AKIA\|ey[A-Za-z]" tests/fixtures/debug-log/` | No matches |
| Security tests pass | `python test_security.py` | All pass |

---

## WBS-101 / WBS-102 issue mapping

| WBS ID | GitHub Issue | Title | Status |
|---|---|---|---|
| WBS-101 | #426 | Define Agent Debug Log browse contract and schema fixtures | Done in this PR |
| WBS-102 | #427 | Define debug log security and redaction policy | Placeholder reserved in `docs/DEBUG-LOG-CONTRACT.md` |
| WBS-103 | #428 | Implement `GET /api/session/{id}/debug-log` backend route | Blocked on WBS-101 |
| WBS-104–110 | #429–435 | UI, schemas, SSE, integration, perf, CI | Blocked on WBS-101+103 |
