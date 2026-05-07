# Repo Sync Matrix

> **Neat-freak sync discipline:** every code change has downstream follow-ups.
> Use this matrix as the canonical reference for what needs updating when.

---

## Change → Follow-up Matrix

| Change type | Docs to update | Memory (learn.py) | Operator surfaces |
|---|---|---|---|
| New behavior / feature | Relevant `docs/*.md` | `learn.py` (pattern or decision) | README / SKILLS.md if user-facing |
| Bug fix / mistake corrected | — | `learn.py mistake` entry | — |
| Architecture decision | `docs/ARCHITECTURE.md` | `learn.py decision` entry | — |
| Hook / enforcement rule change | `docs/HOOKS.md` | `learn.py pattern` entry | — |
| Skill or agent change | `docs/SKILLS.md` | — | skill catalog / `lint-skills.py` |
| Sync config / runtime change | `docs/SKILLS.md` sync section | `learn.py decision` entry | `sync-status.py` output |
| Tentacle complete | tentacle `handoff.md` | `tentacle.py complete` (auto-learns) | `tentacle.py status` |
| Test-only change | — | — | — |
| Doc-only change | — | — | — |

---

## End-of-task Checklist

Run through this before calling `task_complete`:

```
1. Docs         — did behavior change? update docs/ accordingly
2. Memory       — record mistakes / patterns: sk learn
3. Handoff      — if inside a tentacle: sk tentacle handoff <name> "<summary>" --status <STATUS> [--changed-file <path>] --learn
4. Tests        — run tests for any changed Python files
5. Sync          — if sync config or runtime changed: sk sync status --health-check
```

---

## End-of-session Checklist

Optional hygiene at session end:

```
1. Index new sessions  — sk index build
2. Extract knowledge   — sk index extract
3. Stale doc audit     — are any docs now outdated?
4. Open tentacles      — sk tentacle status
```

---

## Sync Surfaces Quick-reference

| Surface | Command |
|---|---|
| Knowledge briefing | `sk briefing "<task>"` |
| Record a learning | `sk learn` |
| Sync runtime status | `sk sync status --health-check` |
| Knowledge insights | `sk index health` |
| Tentacle status | `sk tentacle status` |

---

## Guidance Philosophy

1. **Local-first:** `knowledge.db` is always the source of truth. Remote sync is optional transport.
2. **Advisory, not blocking:** this matrix is a reminder; enforcement lives in hooks.
3. **Learn from mistakes:** every corrected mistake should become a `learn.py` entry so future sessions avoid it.
4. **Docs drift is technical debt:** an undocumented behavior change becomes a bug for future agents.

---

*See also: [docs/HOOKS.md](HOOKS.md) · [docs/SKILLS.md](SKILLS.md) · [docs/ARCHITECTURE.md](ARCHITECTURE.md)*
