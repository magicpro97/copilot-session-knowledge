"""Dispatch middleware for sk — pre/post hooks controlled by SK_HARNESS=1."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

_TELEMETRY_PATH = Path.home() / ".copilot" / "markers" / "harness-telemetry.jsonl"
_TELEMETRY_MAX_BYTES = 1_000_000  # 1 MB
_TELEMETRY_KEEP_LINES = 500


@dataclass
class DispatchContext:
    """Mutable context passed through pre/post dispatch hooks."""

    cmd: str
    script: str
    extra_args: list[str]
    env: dict[str, str]
    start_ms: float = 0.0
    abort: bool = False
    abort_code: int = 0
    result_code: int = 0


# Hook registries — populated by modules that import harness.dispatch
_PRE_HOOKS: list[Callable[[DispatchContext], None]] = []
_POST_HOOKS: list[Callable[[DispatchContext], None]] = []


def _dry_run_pre_hook(ctx: DispatchContext) -> None:
    """Abort dispatch and print command when SK_DRY_RUN=1."""
    if os.environ.get("SK_DRY_RUN") == "1":
        print(f"[sk dry-run] would run: {ctx.script} {' '.join(ctx.extra_args)}", file=sys.stderr)
        ctx.abort = True
        ctx.abort_code = 0


def _timing_start(ctx: DispatchContext) -> None:
    """Record dispatch start time in context."""
    ctx.start_ms = time.time() * 1000


def _timing_end(ctx: DispatchContext) -> None:
    """Log elapsed time and append JSONL telemetry (fail-open)."""
    elapsed_ms = int(time.time() * 1000 - ctx.start_ms)
    if os.environ.get("SK_DEBUG_TIMING") == "1":
        print(f"[sk] {ctx.cmd} completed in {elapsed_ms}ms (rc={ctx.result_code})", file=sys.stderr)
    try:
        _TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Rotate when file exceeds 1 MB
        if _TELEMETRY_PATH.exists() and _TELEMETRY_PATH.stat().st_size > _TELEMETRY_MAX_BYTES:
            lines = _TELEMETRY_PATH.read_text(encoding="utf-8").splitlines()
            _TELEMETRY_PATH.write_text(
                "\n".join(lines[-_TELEMETRY_KEEP_LINES:]) + "\n",
                encoding="utf-8",
            )
        record = json.dumps(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "cmd": ctx.cmd,
                "elapsed_ms": elapsed_ms,
                "rc": ctx.result_code,
            },
            ensure_ascii=False,
        )
        with _TELEMETRY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(record + "\n")
    except Exception:  # noqa: BLE001
        pass  # Telemetry errors are always swallowed


_PRE_HOOKS.append(_dry_run_pre_hook)
_PRE_HOOKS.append(_timing_start)
_POST_HOOKS.append(_timing_end)


def run_with_hooks(
    cmd: str,
    script: str,
    args: list[str],
    tools_dir: str,
    env: dict | None = None,
) -> int:
    """Run script through pre/post hook pipeline. Returns exit code.

    *env* allows callers (e.g. sk._run) to inject a project-specific
    environment (SK_PROJECT_ROOT, SK_DB_PATH).  When omitted the current
    process environment is used, preserving backward compatibility.
    """
    ctx = DispatchContext(
        cmd=cmd,
        script=script,
        extra_args=list(args),
        env=env if env is not None else dict(os.environ),
        start_ms=time.time() * 1000,
    )

    # Run pre-hooks (fail-open: exceptions are logged, dispatch continues)
    for hook in _PRE_HOOKS:
        try:
            hook(ctx)
        except Exception as exc:  # noqa: BLE001
            print(f"[sk harness] pre-hook error: {exc}", file=sys.stderr)
        if ctx.abort:
            return ctx.abort_code

    # Dispatch subprocess
    script_path = os.path.join(tools_dir, ctx.script)
    result = subprocess.run([sys.executable, script_path, *ctx.extra_args], env=ctx.env)
    ctx.result_code = result.returncode

    # Run post-hooks (fail-open)
    for hook in _POST_HOOKS:
        try:
            hook(ctx)
        except Exception as exc:  # noqa: BLE001
            print(f"[sk harness] post-hook error: {exc}", file=sys.stderr)

    return ctx.result_code
