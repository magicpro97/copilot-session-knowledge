# HARNESS.md — Harness Middleware API Reference

> Dispatch middleware for `sk` — pre/post hooks controlled by `SK_HARNESS=1`.
> Source: `harness/dispatch.py`, `harness/meta.py`, `harness/__init__.py`

---

## 1. Overview

The harness package (`harness/`) is a lightweight middleware layer that wraps every `sk` subprocess dispatch with a pre/post hook pipeline.

**When to use `SK_HARNESS=1`:**
- Attach custom audit hooks before/after any `sk` command runs
- Capture timing telemetry for all commands (`~/.copilot/markers/harness-telemetry.jsonl`)
- Dry-run mode: print the resolved command without executing it

**Zero overhead when disabled:** When `SK_HARNESS` is not set to `1`, `sk` routes commands directly to the subprocess without loading the harness package. No performance impact on normal usage.

---

## 2. DispatchContext Reference

`DispatchContext` is the mutable context object passed through every pre and post hook. Defined in `harness/dispatch.py`.

```python
@dataclass
class DispatchContext:
    cmd: str              # The logical sk command name (e.g. "briefing")
    script: str           # The Python script filename (e.g. "briefing.py")
    extra_args: list[str] # Arguments passed to the script
    env: dict[str, str]   # Process environment for the subprocess
    start_ms: float       # Dispatch start time in milliseconds (epoch × 1000)
    abort: bool           # Set True in a pre-hook to cancel dispatch
    abort_code: int       # Exit code to return when abort=True (default 0)
    result_code: int      # Exit code returned by the subprocess (post-hooks only)
```

### Field notes

| Field | Set by | Notes |
|-------|--------|-------|
| `cmd` | `run_with_hooks()` caller | Logical name for telemetry and logging |
| `script` | `run_with_hooks()` caller | Resolved from `CommandMeta.script` |
| `extra_args` | `run_with_hooks()` caller | Mutable — pre-hooks may modify |
| `env` | `run_with_hooks()` or `os.environ` | Mutable — pre-hooks may inject vars |
| `start_ms` | `_timing_start` pre-hook | `time.time() * 1000` at dispatch entry |
| `abort` | Pre-hook | When `True`, dispatch stops; remaining pre-hooks are skipped |
| `abort_code` | Pre-hook | Return code when `abort=True` |
| `result_code` | `run_with_hooks()` after subprocess | Available to post-hooks only |

---

## 3. Hook Registration API

> **Note:** `register_pre_hook` / `register_post_hook` / `list_hooks` are available as of the public API in `harness/__init__.py`. Full integration with `SK_HARNESS=1` auto-loading requires issue **#682** to be implemented.

### `harness.register_pre_hook(fn)`

Register a callable called **before** the subprocess starts.

```python
def register_pre_hook(fn: Callable[[DispatchContext], None]) -> None
```

- `fn` receives the mutable `DispatchContext`
- Set `ctx.abort = True` to cancel the dispatch; set `ctx.abort_code` for the exit code
- Exceptions are caught and logged to stderr (fail-open); dispatch continues unless `ctx.abort`

### `harness.register_post_hook(fn)`

Register a callable called **after** the subprocess completes.

```python
def register_post_hook(fn: Callable[[DispatchContext], None]) -> None
```

- `fn` receives the mutable `DispatchContext` with `result_code` populated
- Exceptions are caught and logged to stderr (fail-open)

### `harness.list_hooks()`

Return the names of all currently registered hooks.

```python
def list_hooks() -> dict:
    # Returns: {"pre": ["hook_name", ...], "post": ["hook_name", ...]}
```

### Hook lifecycle

```
sk dispatch call
      │
      ▼
  _PRE_HOOKS  ←─ registered via register_pre_hook()
  [_dry_run_pre_hook, _timing_start, ...user hooks...]
      │
      │  ctx.abort? → return ctx.abort_code
      │
      ▼
  subprocess.run(script, extra_args, env=ctx.env)
      │
      ▼
  _POST_HOOKS  ←─ registered via register_post_hook()
  [_timing_end, ...user hooks...]
      │
      ▼
  return ctx.result_code
```

### Built-in hooks

| Hook | Registry | Trigger | Behaviour |
|------|----------|---------|-----------|
| `_dry_run_pre_hook` | PRE | `SK_DRY_RUN=1` | Prints `[sk dry-run] would run: …` and sets `ctx.abort=True` |
| `_timing_start` | PRE | always | Records `ctx.start_ms = time.time() * 1000` |
| `_timing_end` | POST | always | Logs elapsed time (if `SK_DEBUG_TIMING=1`) and appends JSONL telemetry |

---

## 4. Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SK_HARNESS` | `(unset)` | Set to `1` to enable middleware hooks |
| `SK_DEBUG_TIMING` | `(unset)` | Set to `1` to print `[sk] <cmd> completed in <N>ms (rc=<N>)` to stderr |
| `SK_DRY_RUN` | `(unset)` | Set to `1` to print the resolved command without executing it |
| `SK_TOOLS_DIR` | `~/.copilot/tools` | Override the tools directory path |

Inspect or set these at runtime with `sk harness config`:

```bash
sk harness config list
sk harness config get SK_HARNESS
sk harness config set SK_HARNESS 1
```

---

## 5. Telemetry

When the harness is active, `_timing_end` appends one JSONL record per dispatch.

**File location:** `~/.copilot/markers/harness-telemetry.jsonl`

**Record schema:**

```json
{"ts": "2025-01-01T12:00:00Z", "cmd": "briefing", "elapsed_ms": 342, "rc": 0}
```

| Field | Type | Description |
|-------|------|-------------|
| `ts` | `str` | ISO 8601 UTC timestamp (`%Y-%m-%dT%H:%M:%SZ`) |
| `cmd` | `str` | Logical command name |
| `elapsed_ms` | `int` | Elapsed time in milliseconds |
| `rc` | `int` | Subprocess exit code |

**Rotation policy:** When the file exceeds **1 MB**, the oldest lines are dropped and only the last **500 lines** are kept. Telemetry errors are always swallowed (fail-open).

---

## 6. CommandMeta Reference

`CommandMeta` is an immutable dataclass describing a single `sk` command. Defined in `harness/meta.py`. Used as values in `sk.py`'s `_DIRECT` registry.

```python
@dataclass(frozen=True)
class CommandMeta:
    script: str                   # Python script filename (e.g. "briefing.py"); None for native-binary-only commands
    description: str = ""         # Human-readable description shown in sk help
    tags: tuple[str, ...] = ()    # Searchable tags (used by sk harness show --tag)
    aliases: tuple[str, ...] = () # Alternative command names
    group: str = "general"        # Logical group (used for organisation)
    experimental: bool = False    # Mark command as experimental
```

`str(meta)` returns `meta.script` for backward compatibility with `_run(str(entry), rest)`.

---

## 7. sk harness Subcommands

### `sk harness show [--tag TAG] [--json]`

List all registered `sk` commands with metadata.

- `--tag TAG` — filter to commands with the given tag
- `--json` — output as JSON array of `{cmd, script, description, tags}` objects

### `sk harness check [--json]`

Verify every script registered in `_DIRECT` and `_GROUPS` exists on disk.

- Exit 0 when all scripts are present
- Exit 1 when any are missing; prints missing `{cmd, script}` entries
- `--json` — output `{"ok": N, "missing": [...]}` object

### `sk harness doctor [--json]`

Run 5 health checks:

| Check | What it tests |
|-------|--------------|
| Scripts | All registered scripts present on disk |
| DB | `~/.copilot/session-state/knowledge.db` accessible via SQLite |
| Project root | `sk.py` exists in the resolved tools directory |
| Hooks | `~/.copilot/hooks/` directory exists |
| Python version | Python ≥ 3.10 |

- Exit 0 when all 5 checks pass; exit 1 otherwise
- `--json` — structured output with per-check details

### `sk harness config list|get|set`

Manage harness environment variables in the current process.

```bash
sk harness config list               # Print all vars with current values
sk harness config get SK_HARNESS     # Print value of one var
sk harness config set SK_HARNESS 1   # Set var in current process
```

---

## 8. Examples

```bash
# Enable harness middleware
SK_HARNESS=1 sk briefing "task"

# Dry-run mode — show command without executing
SK_HARNESS=1 SK_DRY_RUN=1 sk learn --pattern "Test" "Content"

# Debug timing output
SK_HARNESS=1 SK_DEBUG_TIMING=1 sk query "search"

# Inspect registered hooks
python3 -c "import harness; print(harness.list_hooks())"

# Check all scripts are present
sk harness check

# Full health check as JSON
sk harness doctor --json

# Show commands tagged 'session'
sk harness show --tag session
```

```python
# Register a custom pre-hook (requires SK_HARNESS=1 and issue #682 for auto-load)
import sys
import harness

def my_audit_hook(ctx: harness.DispatchContext) -> None:
    print(f"[audit] Running: {ctx.cmd}", file=sys.stderr)

harness.register_pre_hook(my_audit_hook)
```

```python
# Block specific commands via pre-hook
import harness

def block_destructive(ctx: harness.DispatchContext) -> None:
    if ctx.cmd in {"drop", "reset"}:
        ctx.abort = True
        ctx.abort_code = 1

harness.register_pre_hook(block_destructive)
```
