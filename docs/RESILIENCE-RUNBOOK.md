# Resilience Runbook

> Operator-level recovery guide for compaction/state-loss, session interruption, and
> quota/rate-limit failures in copilot-session-knowledge goal orchestration.
>
> **Prerequisites:** familiarity with `sk tentacle goal` commands — see [docs/USAGE.md](USAGE.md#goal-orchestration).
> **Playbook home:** [docs/OPERATOR-PLAYBOOK.md](OPERATOR-PLAYBOOK.md)

---

## Table of Contents

1. [Background: What Survives and What Does Not](#1-background-what-survives-and-what-does-not)
2. [Compaction / State-Loss Recovery](#2-compaction--state-loss-recovery)
3. [Session Interruption Recovery](#3-session-interruption-recovery)
4. [Quota and Rate-Limit Recovery](#4-quota-and-rate-limit-recovery)
5. [Database Schema Backup and Rollback](#5-database-schema-backup-and-rollback)
6. [Goal Status Reference](#6-goal-status-reference)
7. [Recovery Decision Tree (Quick Reference)](#7-recovery-decision-tree-quick-reference)

---

## 1. Background: What Survives and What Does Not

**Source of truth:** research findings from issues #181 (compaction inventory) and #183 (interruption/quota failure matrix).

| Item | Survives compaction / session end? | Notes |
|------|------------------------------------|-------|
| `goal.json` on disk | ✅ Yes | File persists; `active`/`awaiting-gate` status transitions to `paused` on session end |
| Per-attempt criteria results in `goal.json` | ✅ Yes | Written atomically at each run |
| Goal lock file (`.octogent/goal.json.lock`) | ✅ Auto-recovers | Dead-PID detection → auto-cleared |
| In-context working memory | ❌ No | Lost on compaction or session end |
| Dispatch markers (stale after kill) | ❌ Possibly stale | FM-2/FM-4: must clean up manually |
| Auto-pause breadcrumb | ✅ Written on session end | `.octogent/goal-resume-breadcrumb.json`; goal status set to `paused` (#184) |
| Auto-resume hint on session start | ✅ Shown at session start | `auto-briefing` hook reads breadcrumb and prints resume banner (#185) |

**Key implication:** after any interruption, always start recovery by reading `goal.json` directly — it is the single source of truth.

```bash
sk tentacle goal status              # Human-readable current state
sk tentacle goal status --format json  # Machine-readable (pipe/parse)
# fallback: python3 ~/.copilot/tools/tentacle.py goal status [--format json]
```

---

## 2. Compaction / State-Loss Recovery

Compaction means the in-context session was truncated or replaced. `goal.json` is safe; pick up from where it left off.

### 2.1 Diagnose

```bash
# Step 1: Read current goal state
sk tentacle goal status

# Step 2: Check which tentacles are still in flight
sk tentacle status
# fallback: python3 ~/.copilot/tools/tentacle.py status
```

**Decision: what does `goal status` show?**

| Goal status | Action |
|-------------|--------|
| `active` | [Resume active goal](#22-resume-an-active-goal) |
| `paused` | [Resume paused goal](#23-resume-a-paused-goal) |
| `awaiting-gate` | [Clear gate block first](#25-clear-an-awaiting-gate-block), then resume |
| `needs-human` | [Investigate, fix, then resume](#44-after-cooldown-or-fix-resume-the-goal) |
| `completed` or `abandoned` | No action — goal reached a terminal state |

### 2.2 Resume an Active Goal

Goal is `active`; in-context memory was lost but no state transition needed.

```bash
# Verify budget and iteration counter
sk tentacle goal status

# Re-dispatch new tentacle wave for remaining work
# (inspect handoffs to understand what completed before compaction)
sk tentacle status

# After dispatching new tentacles, advance the loop
sk tentacle goal eval --decision continue --notes "Resuming after compaction at iteration N"
# fallback: python3 ~/.copilot/tools/tentacle.py goal eval --decision continue --notes "..."
```

### 2.3 Resume a Paused Goal

```bash
# Re-activate goal (status: paused → active)
sk tentacle goal resume
# fallback: python3 ~/.copilot/tools/tentacle.py goal resume

# If some tentacles in the current iteration should be retried:
sk tentacle goal resume --reset-failed

# If the iteration needs to rewind entirely:
sk tentacle goal resume --from-iteration N
# fallback: python3 ~/.copilot/tools/tentacle.py goal resume --from-iteration N
```

> `goal resume` does **not** re-run success criteria — it only changes the status to `active`.
> Always follow with `goal eval` or a new tentacle wave.

### 2.4 Clean Up Stale Dispatch Markers

After compaction or abrupt kill, stale markers may block `git commit` or confuse the orchestrator.

```bash
# Inspect stale markers
python3 ~/.copilot/tools/tentacle.py marker-cleanup
# Apply cleanup (removes entries whose TTL has expired or whose PID is dead):
python3 ~/.copilot/tools/tentacle.py marker-cleanup --apply
```

Markers auto-expire after 4 hours. For immediate cleanup, use `--apply`.

### 2.5 Clear an Awaiting-Gate Block

```bash
# Show which gate is blocking
sk tentacle goal status

# Approve the gate (after human review)
sk tentacle goal gate approve <gate-id> --reason "Reviewed OK post-compaction"
# fallback: python3 ~/.copilot/tools/tentacle.py goal gate approve <gate-id> --reason "..."

# Re-activate goal
sk tentacle goal resume
# fallback: python3 ~/.copilot/tools/tentacle.py goal resume
```

---

## 3. Session Interruption Recovery

**Research finding (#183):** no compaction-specific hook fires. The session-end hook writes a pause breadcrumb (`.octogent/goal-resume-breadcrumb.json`) and sets the goal to `paused`; the `auto-briefing` rule shows a resume banner on the next session start (#184/#185). Use the procedures below for manual recovery when the breadcrumb is absent or additional intervention is needed.

### 3.1 Failure Mode Quick Reference

| FM | Trigger | Goal state after | Retryable | Recovery section |
|----|---------|-----------------|-----------|-----------------|
| FM-1 | Clean session end (ctrl+D / timeout) | `paused` (breadcrumb written by session-end hook #184) | N/A | [§3.2](#32-clean-session-end-fm-1) |
| FM-2 | Abrupt process kill (SIGKILL / OOM) | `active`; dispatch marker may be stale | Yes | [§3.3](#33-abrupt-kill-fm-2) |
| FM-3 | Ctrl+C in running process | `active`; last per-attempt write survives | Yes | [§3.4](#34-ctrlc-fm-3) |
| FM-4 | Network failure mid-dispatch | `active`; tentacle stuck waiting | Yes | [§3.5](#35-network-failure-fm-4) |

### 3.2 Clean Session End (FM-1)

The session-end hook writes `.octogent/goal-resume-breadcrumb.json` and sets goal status to `paused`. On the next session start, the `auto-briefing` rule prints a resume banner. Run `sk tentacle goal resume` to re-activate.

```bash
# Start a new session — auto-briefing prints the resume banner automatically.
# Then:
sk tentacle goal resume            # Re-activate paused goal

sk tentacle goal resilience-status # Compact health view (per-tentacle state)

# Identify what was in-flight
sk tentacle status                # Show tentacle states

# Continue normal goal-loop
sk tentacle goal next-iter        # Advisory: what to do next
# fallback: python3 ~/.copilot/tools/tentacle.py goal next-iter
```

### 3.3 Abrupt Kill (FM-2)

```bash
# Step 1: Check for stale lock (auto-recovers on next access)
sk tentacle goal status           # Lock auto-cleared if PID dead

# Step 2: Clean stale dispatch markers
python3 ~/.copilot/tools/tentacle.py marker-cleanup --apply

# Step 3: Check tentacle states; re-dispatch any that were lost
sk tentacle status
```

### 3.4 Ctrl+C (FM-3)

Identical to FM-1 recovery. The last written per-attempt state survives.

```bash
sk tentacle goal status
sk tentacle goal next-iter
# fallback: python3 ~/.copilot/tools/tentacle.py goal next-iter
```

### 3.5 Network Failure Mid-Dispatch (FM-4)

The goal stays `active` but a tentacle agent may have never started.

```bash
# Step 1: Identify the stuck tentacle
sk tentacle status

# Step 2: Clean stale dispatch markers
python3 ~/.copilot/tools/tentacle.py marker-cleanup --apply

# Step 3: Re-dispatch the tentacle
# (Use the same tentacle name if the worktree still exists, or create a new tentacle)
sk tentacle goal link <tentacle-name>    # If tentacle completed elsewhere
# fallback: python3 ~/.copilot/tools/tentacle.py goal link <tentacle-name>
```

---

## 4. Quota and Rate-Limit Recovery

**Research finding (#183):** quota exhaustion (FM-5) and rate limiting (FM-6) appear as non-zero exit codes or text in tentacle output/handoff. The goal CLI does not automatically classify them today (#187 will add this). Use the procedures below.

### 4.1 Identify Quota / Rate-Limit Failure

```bash
# Check goal status and recent tentacle handoffs
sk tentacle goal status

# Read tentacle handoff for error details
cat .octogent/tentacles/<tentacle-name>/handoff.md

# Check verify-loop output if running via verify-loop
sk tentacle goal criteria list
# fallback: python3 ~/.copilot/tools/tentacle.py goal criteria list
```

**Signals that indicate quota / rate-limit:**
- HTTP 429 responses in tentacle output
- Text containing `rate limit`, `quota exceeded`, `too many requests`, `billing`
- Verify-loop fails with identical output 3 times → stall detection triggers

### 4.2 Quota Exhaustion (FM-5)

```bash
# Step 1: Pause the goal manually (no auto-pause today)
sk tentacle goal eval --decision pause --notes "Paused: quota exhaustion; cooldown required"
# fallback: python3 ~/.copilot/tools/tentacle.py goal eval --decision pause --notes "..."

# Step 2: Mark affected tentacle BLOCKED with a handoff note
sk tentacle handoff <tentacle-name> "Quota exhausted — blocked" --status BLOCKED
# fallback: python3 ~/.copilot/tools/tentacle.py handoff <tentacle-name> "Quota exhausted — blocked" --status BLOCKED
```

**After cooldown / quota reset:**

```bash
# Step 3: Resume goal
sk tentacle goal resume
# fallback: python3 ~/.copilot/tools/tentacle.py goal resume

# Step 4: Re-run failed tentacle or reset it and re-dispatch
sk tentacle goal resume --reset-failed
```

### 4.3 Rate Limiting in Verify-Loop (FM-6)

Rate-limit errors that repeat identically 3 times trigger stall detection.

```bash
# Without --escalate: verify-loop exits with code 1, no state written
sk tentacle goal verify-loop --max-retries 3 --retry-delay 60
# fallback: python3 ~/.copilot/tools/tentacle.py goal verify-loop --max-retries 3 --retry-delay 60

# With --escalate: goal status set to needs-human after stall
sk tentacle goal verify-loop --escalate --max-retries 3 --retry-delay 60
# fallback: python3 ~/.copilot/tools/tentacle.py goal verify-loop --escalate --max-retries 3 --retry-delay 60
```

**When needs-human is set after escalation:**

```bash
# Step 1: Inspect failing criteria
sk tentacle goal criteria list
# fallback: python3 ~/.copilot/tools/tentacle.py goal criteria list

# Step 2: Inspect full goal state
sk tentacle goal status --format json
# fallback: python3 ~/.copilot/tools/tentacle.py goal status --format json

# Step 3: Wait for rate-limit window to clear, then resume
sk tentacle goal resume
# fallback: python3 ~/.copilot/tools/tentacle.py goal resume

# Step 4: Re-run verify-loop with backoff
sk tentacle goal verify-loop --retry-delay 120
# fallback: python3 ~/.copilot/tools/tentacle.py goal verify-loop --retry-delay 120
```

### 4.4 Budget Overrun (FM-7)

Budget overrun is **advisory-only** today — the goal remains `active` and no automatic state change occurs. (#186 will add structured escalation.)

```bash
# Check current budget
sk tentacle goal budget
# fallback: python3 ~/.copilot/tools/tentacle.py goal budget

# Check goal eval advisory warning
sk tentacle goal eval --decision continue
# If over budget, a WARNING is printed but goal continues

# Operator decision: continue or abandon
# To continue despite over-budget (operator accepts risk):
sk tentacle goal eval --decision continue --notes "Operator override: budget exceeded, continuing"

# To stop cleanly:
sk tentacle goal eval --decision abandon --notes "Budget overrun; abandoning goal"
# fallback: python3 ~/.copilot/tools/tentacle.py goal eval --decision abandon --notes "..."
```

### 4.5 Stall in Verify-Loop (FM-8 / FM-9)

Stall = same failure output repeated ≥ 3 times across retries.

```bash
# Run verify-loop with stall detection and escalation
sk tentacle goal verify-loop --escalate
# fallback: python3 ~/.copilot/tools/tentacle.py goal verify-loop --escalate

# Goal set to needs-human; review next steps:
sk tentacle goal status

# Fix the underlying failure (manual tentacle, code fix, etc.)

# Then resume:
sk tentacle goal resume
sk tentacle goal verify-loop --escalate
```

> **Note:** stall usually indicates a non-transient failure (FM-8). For transient failures (rate-limit), add `--retry-delay` and reduce `--max-retries` to avoid false stall triggers.

---

## 5. Database Schema Backup and Rollback

Use this before manual database repair, schema rehearsals, or risky local upgrades.
`migrate.py --backup-only` uses SQLite's online backup API, so it captures a
consistent copy even when the source database is in WAL mode.

```bash
# Create a rollback copy without applying migrations
python migrate.py ~/.copilot/session-state/knowledge.db --backup-only --backup-path /tmp/knowledge.db.backup

# Apply migrations after the backup exists
python migrate.py ~/.copilot/session-state/knowledge.db
```

If migration reports a corrupt database or schema failure, do not keep retrying against
the same file. Restore the backup, or move the bad database aside and let migration
bootstrap a fresh schema. Stop all session-knowledge writers first, then remove or move
the matching WAL sidecars so SQLite cannot replay stale `knowledge.db-wal` state after
the main DB file is restored or replaced:

```bash
# Stop active writers before manipulating DB files
# Examples: stop `sk watch`, sync daemons, launchd services, or CI jobs using this DB.

# Restore known-good backup
rm -f ~/.copilot/session-state/knowledge.db-wal ~/.copilot/session-state/knowledge.db-shm
cp /tmp/knowledge.db.backup ~/.copilot/session-state/knowledge.db

# Or preserve the bad file for investigation and bootstrap a new DB
mv ~/.copilot/session-state/knowledge.db ~/.copilot/session-state/knowledge.db.corrupt
mv ~/.copilot/session-state/knowledge.db-wal ~/.copilot/session-state/knowledge.db-wal.corrupt 2>/dev/null || true
mv ~/.copilot/session-state/knowledge.db-shm ~/.copilot/session-state/knowledge.db-shm.corrupt 2>/dev/null || true
python migrate.py ~/.copilot/session-state/knowledge.db
```

When validating a rollback, run migration twice: the first run should apply missing
versions, and the second run should report `Schema up to date`.

---

## 6. Goal Status Reference

| Status | Meaning | How to resume |
|--------|---------|--------------|
| `active` | Goal in progress | No action needed; dispatch tentacles and eval |
| `paused` | Explicitly paused by operator/eval | `sk tentacle goal resume` |
| `awaiting-gate` | Blocked on a pending/rejected gate | `goal gate approve <id>`, then `goal resume` |
| `needs-human` | Stall, escalation, or human decision required | Fix root cause, then `goal resume` |
| `completed` | Terminal — goal met | No recovery; close the issue |
| `abandoned` | Terminal — goal stopped | No recovery unless restarted with `goal init` |

---

## 7. Recovery Decision Tree (Quick Reference)

```
After compaction / new session:
  └─ Run: sk tentacle goal status
        │
        ├─ active        → Read sk tentacle status; run sk tentacle goal next-iter
        ├─ paused        → sk tentacle goal resume [--reset-failed] [--from-iteration N]
        ├─ awaiting-gate → sk tentacle goal gate approve <id> --reason "..." → goal resume
        ├─ needs-human   → diagnose: goal criteria list + goal status --format json
        │                  fix root cause → goal resume → goal verify-loop [--escalate]
        ├─ completed     → no action
        └─ abandoned     → no action (or goal init for a fresh start)

Quota / rate-limit in tentacle:
  └─ Manual pause: goal eval --decision pause --notes "quota; cooldown"
        └─ After cooldown: goal resume → re-dispatch or --reset-failed

Rate-limit in verify-loop (identical failures 3x):
  └─ goal verify-loop --escalate → needs-human
        └─ Wait for window → goal resume → goal verify-loop --retry-delay 120

Budget overrun:
  └─ Advisory WARNING only (no status change today — see #186)
        └─ Operator decides: continue (default) or goal eval --decision abandon

Stale dispatch markers (after kill/network failure):
  └─ python3 ~/.copilot/tools/tentacle.py marker-cleanup --apply
```

---

## Future Work

The following gaps are tracked in the implementation backlog and are **not yet available** as operator commands:

| Gap | Issue | Expected behavior (when implemented) |
|-----|-------|--------------------------------------|
| Structured budget escalation | #186 | `goal eval --decision continue` escalates to `needs-human` when over budget, with `--force-over-budget` override |
| Quota/rate-limit BLOCKED handoff + retry queue | #187 | `tentacle handoff --status BLOCKED` with quota signals adds to `.octogent/retry-queue.json`; `goal eval` surfaces retry-blocked tentacles separately |
