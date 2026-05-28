"""Dispatch middleware for sk — pre/post hooks controlled by SK_HARNESS=1."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")


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


_PRE_HOOKS.append(_dry_run_pre_hook)


def run_with_hooks(cmd: str, script: str, args: list[str], tools_dir: str) -> int:
    """Run script through pre/post hook pipeline. Returns exit code."""
    ctx = DispatchContext(
        cmd=cmd,
        script=script,
        extra_args=list(args),
        env=dict(os.environ),
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
