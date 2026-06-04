#!/usr/bin/env python3
"""
test_orchestrator.py — Tests for install.deploy_orchestrator() and helpers.

Covers:
  - _extract_orchestrator_policy() returns the marker-delimited span (markers
    included) and drops the editable preamble above the START marker
  - _inject_orchestrator_policy() injects, replaces in place, and preserves
    surrounding user content idempotently
  - deploy_orchestrator() copies all Claude worker subagent templates into
    ~/.claude/agents/ AND injects the orchestrator policy into BOTH host global
    instruction files (~/.claude/CLAUDE.md and ~/.copilot/copilot-instructions.md)
  - The deploy is idempotent — a second run produces no changes and never
    duplicates the policy block in either file
  - Injecting into pre-existing files preserves user content and appends after it
  - Re-running after a stale policy block replaces it in place
  - deploy_claude_agents() remains a working deprecated alias

Run: python3 tests/test_orchestrator.py
"""

import importlib.util
import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent

SCRATCH = REPO / ".test-scratch" / "orchestrator-tests"
SCRATCH.mkdir(parents=True, exist_ok=True)

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  \u2705 {name}")
    else:
        FAIL += 1
        print(f"  \u274c {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Load install module
# ---------------------------------------------------------------------------

_script = REPO / "install.py"
_spec = importlib.util.spec_from_file_location("_install_orch", _script)
_install = importlib.util.module_from_spec(_spec)
_saved_argv = sys.argv[:]
sys.argv = [str(_script)]
try:
    _spec.loader.exec_module(_install)
finally:
    sys.argv = _saved_argv


# Stub the manifest writer — scratch paths live outside HOME, so recording would
# only emit "outside HOME" warnings; stubbing keeps the run clean and isolated.
_install._record_managed_paths = lambda *a, **k: 0


def _redirect_hosts(base: Path) -> None:
    """Point the module-level host paths at an isolated scratch home."""
    claude_dir = base / ".claude"
    copilot_dir = base / ".copilot"
    _install.CLAUDE_DIR = claude_dir
    _install.CLAUDE_AGENTS_DIR = claude_dir / "agents"
    _install.CLAUDE_MEMORY = claude_dir / "CLAUDE.md"
    _install.COPILOT_GLOBAL_INSTRUCTIONS = copilot_dir / "copilot-instructions.md"


_orig = (
    _install.CLAUDE_DIR,
    _install.CLAUDE_AGENTS_DIR,
    _install.CLAUDE_MEMORY,
    _install.COPILOT_GLOBAL_INSTRUCTIONS,
)

_TEMPLATE_AGENTS = sorted(p.name for p in _install._CLAUDE_AGENTS_TEMPLATE_DIR.glob("*.md"))
_START = _install._ORCH_POLICY_MARKER_START
_END = _install._ORCH_POLICY_MARKER_END


# ── 1. _extract_orchestrator_policy ──────────────────────────────────────────

print("\n\U0001f9e9 _extract_orchestrator_policy")

policy_text = _install._ORCH_POLICY_TEMPLATE.read_text(encoding="utf-8")
block = _install._extract_orchestrator_policy(policy_text)

test("policy block is non-empty", bool(block))
test("policy block starts with START marker", block.startswith(_START))
test("policy block ends with END marker", block.endswith(_END))
test(
    "editable preamble above START marker is excluded",
    policy_text.index(_START) > 0 and not block.startswith(policy_text[:10]),
)
test("missing markers yields empty string", _install._extract_orchestrator_policy("no markers here") == "")


# ── 2. deploy_orchestrator — fresh install ───────────────────────────────────

print("\n\U0001f680 deploy_orchestrator — fresh install (both hosts)")

home1 = SCRATCH / "home-fresh"
if home1.exists():
    import shutil as _sh

    _sh.rmtree(home1, ignore_errors=True)
home1.mkdir(parents=True, exist_ok=True)
_redirect_hosts(home1)

_install.deploy_orchestrator()

copied = sorted(p.name for p in _install.CLAUDE_AGENTS_DIR.glob("*.md"))
test("all Claude worker templates copied", copied == _TEMPLATE_AGENTS, f"{copied} != {_TEMPLATE_AGENTS}")
test("at least 6 workers shipped", len(copied) >= 6)

claude_mem = _install.CLAUDE_MEMORY.read_text(encoding="utf-8")
copilot_mem = _install.COPILOT_GLOBAL_INSTRUCTIONS.read_text(encoding="utf-8")
test("CLAUDE.md created", _install.CLAUDE_MEMORY.is_file())
test("Copilot global instructions created", _install.COPILOT_GLOBAL_INSTRUCTIONS.is_file())
test("policy injected into CLAUDE.md", _START in claude_mem and _END in claude_mem)
test("policy injected into Copilot global instructions", _START in copilot_mem and _END in copilot_mem)
test("CLAUDE.md single START marker", claude_mem.count(_START) == 1)
test("Copilot single START marker", copilot_mem.count(_START) == 1)
test("CLAUDE.md created with Claude header", claude_mem.startswith("# Claude Code Instructions"))
test("Copilot created with Copilot header", copilot_mem.startswith("# Global Copilot Instructions"))


# ── 3. Idempotency ───────────────────────────────────────────────────────────

print("\n\U0001f501 deploy_orchestrator — idempotent re-run")

_install.deploy_orchestrator()
claude_mem2 = _install.CLAUDE_MEMORY.read_text(encoding="utf-8")
copilot_mem2 = _install.COPILOT_GLOBAL_INSTRUCTIONS.read_text(encoding="utf-8")
test("CLAUDE.md unchanged on second run", claude_mem == claude_mem2)
test("Copilot instructions unchanged on second run", copilot_mem == copilot_mem2)
test("no duplicate policy in CLAUDE.md", claude_mem2.count(_START) == 1)
test("no duplicate policy in Copilot instructions", copilot_mem2.count(_START) == 1)
test(
    "agent set unchanged on second run",
    sorted(p.name for p in _install.CLAUDE_AGENTS_DIR.glob("*.md")) == copied,
)


# ── 4. Pre-existing content preserved ────────────────────────────────────────

print("\n\U0001f4dd deploy_orchestrator — preserves existing instruction content")

home2 = SCRATCH / "home-existing"
if home2.exists():
    import shutil as _sh2

    _sh2.rmtree(home2, ignore_errors=True)
(home2 / ".claude").mkdir(parents=True, exist_ok=True)
(home2 / ".copilot").mkdir(parents=True, exist_ok=True)
_redirect_hosts(home2)

_install.CLAUDE_MEMORY.write_text("# My Claude Memory\n\nClaude personal notes.\n", encoding="utf-8")
_install.COPILOT_GLOBAL_INSTRUCTIONS.write_text("# My Copilot Rules\n\nCopilot personal notes.\n", encoding="utf-8")

_install.deploy_orchestrator()
c_final = _install.CLAUDE_MEMORY.read_text(encoding="utf-8")
p_final = _install.COPILOT_GLOBAL_INSTRUCTIONS.read_text(encoding="utf-8")
test("Claude user content preserved", "Claude personal notes." in c_final)
test("Copilot user content preserved", "Copilot personal notes." in p_final)
test("Claude policy appended after user content", c_final.index("personal notes") < c_final.index(_START))
test("Copilot policy appended after user content", p_final.index("personal notes") < p_final.index(_START))


# ── 5. Stale policy block replaced in place ──────────────────────────────────

print("\u267b\ufe0f  deploy_orchestrator — replaces stale policy in place")

stale = "# Header\n\n" + _START + "\nOLD STALE POLICY TEXT\n" + _END + "\n\n# Footer\n"
_install.CLAUDE_MEMORY.write_text(stale, encoding="utf-8")
_install.deploy_orchestrator()
replaced = _install.CLAUDE_MEMORY.read_text(encoding="utf-8")
test("stale policy text removed", "OLD STALE POLICY TEXT" not in replaced)
test("header preserved on replace", replaced.startswith("# Header"))
test("footer preserved on replace", replaced.rstrip().endswith("# Footer"))
test("single marker after replace", replaced.count(_START) == 1)


# ── 5b. Malformed file: START present but END missing ────────────────────────

print("\n\U0001f9ea deploy_orchestrator — malformed (START without END) self-heals")

_install.CLAUDE_MEMORY.write_text("# Doc\n\n" + _START + "\nhand-edited, END deleted\n", encoding="utf-8")
_install.deploy_orchestrator()
healed1 = _install.CLAUDE_MEMORY.read_text(encoding="utf-8")
test("malformed inject does not silently noop (END now present)", _END in healed1)
# First pass appends a clean block, leaving the stray START → two STARTs.
# A second pass must self-heal to exactly one well-formed block.
_install.deploy_orchestrator()
healed2 = _install.CLAUDE_MEMORY.read_text(encoding="utf-8")
test("self-heals to single START after re-run", healed2.count(_START) == 1)
test("self-heals to single END after re-run", healed2.count(_END) == 1)


# ── 6. Deprecated alias still works ──────────────────────────────────────────

print("\n\U0001f517 deploy_claude_agents — deprecated alias")

home3 = SCRATCH / "home-alias"
if home3.exists():
    import shutil as _sh3

    _sh3.rmtree(home3, ignore_errors=True)
home3.mkdir(parents=True, exist_ok=True)
_redirect_hosts(home3)

_install.deploy_claude_agents()
test("alias copies workers", len(list(_install.CLAUDE_AGENTS_DIR.glob("*.md"))) >= 6)
test("alias injects Claude policy", _START in _install.CLAUDE_MEMORY.read_text(encoding="utf-8"))
test("alias injects Copilot policy", _START in _install.COPILOT_GLOBAL_INSTRUCTIONS.read_text(encoding="utf-8"))


# ── Restore + Summary ────────────────────────────────────────────────────────

(
    _install.CLAUDE_DIR,
    _install.CLAUDE_AGENTS_DIR,
    _install.CLAUDE_MEMORY,
    _install.COPILOT_GLOBAL_INSTRUCTIONS,
) = _orig

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed")

import shutil

try:
    shutil.rmtree(SCRATCH, ignore_errors=True)
except Exception:
    pass

sys.exit(1 if FAIL else 0)
