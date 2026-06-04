#!/usr/bin/env python3
"""
test_claude_agents.py — Tests for install.deploy_claude_agents().

Covers:
  - _extract_orchestrator_policy() returns the marker-delimited span (markers
    included) and drops the editable preamble above the START marker
  - deploy_claude_agents() copies all worker subagent templates into
    ~/.claude/agents/ and injects the orchestrator policy into ~/.claude/CLAUDE.md
  - The deploy is idempotent — a second run produces no changes and never
    duplicates the policy block
  - Injecting into a pre-existing CLAUDE.md preserves the user's content and
    appends the policy after it
  - Re-running after an out-of-date policy block replaces it in place

Run: python3 tests/test_claude_agents.py
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

SCRATCH = REPO / ".test-scratch" / "claude-agents-tests"
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
_spec = importlib.util.spec_from_file_location("_install_ca", _script)
_install = importlib.util.module_from_spec(_spec)
_saved_argv = sys.argv[:]
sys.argv = [str(_script)]
try:
    _spec.loader.exec_module(_install)
finally:
    sys.argv = _saved_argv


# Keep the manifest untouched — the scratch paths live outside HOME anyway,
# but stubbing avoids noisy "outside HOME" warnings during the run.
_install._record_managed_paths = lambda *a, **k: 0


def _redirect_claude_home(base: Path) -> None:
    """Point the module-level Claude paths at an isolated scratch home."""
    claude_dir = base / ".claude"
    _install.CLAUDE_DIR = claude_dir
    _install.CLAUDE_AGENTS_DIR = claude_dir / "agents"
    _install.CLAUDE_MEMORY = claude_dir / "CLAUDE.md"


_orig_dir = _install.CLAUDE_DIR
_orig_agents = _install.CLAUDE_AGENTS_DIR
_orig_memory = _install.CLAUDE_MEMORY

# Number of worker subagent templates shipped in the repo.
_TEMPLATE_AGENTS = sorted(p.name for p in _install._CLAUDE_AGENTS_TEMPLATE_DIR.glob("*.md"))


# ── 1. _extract_orchestrator_policy ──────────────────────────────────────────

print("\n\U0001f9e9 _extract_orchestrator_policy")

policy_text = _install._CLAUDE_POLICY_TEMPLATE.read_text(encoding="utf-8")
block = _install._extract_orchestrator_policy(policy_text)

test("policy block is non-empty", bool(block))
test("policy block starts with START marker", block.startswith(_install._ORCH_POLICY_MARKER_START))
test("policy block ends with END marker", block.endswith(_install._ORCH_POLICY_MARKER_END))
test(
    "preamble above START marker is excluded",
    policy_text.index(_install._ORCH_POLICY_MARKER_START) > 0 and not block.startswith(policy_text[:10]),
)
test("missing markers yields empty string", _install._extract_orchestrator_policy("no markers here") == "")


# ── 2. deploy_claude_agents — fresh install ──────────────────────────────────

print("\n\U0001f680 deploy_claude_agents — fresh install")

home1 = SCRATCH / "home-fresh"
if home1.exists():
    import shutil as _sh

    _sh.rmtree(home1, ignore_errors=True)
home1.mkdir(parents=True, exist_ok=True)
_redirect_claude_home(home1)

_install.deploy_claude_agents()

copied = sorted(p.name for p in _install.CLAUDE_AGENTS_DIR.glob("*.md"))
test("all worker templates copied", copied == _TEMPLATE_AGENTS, f"{copied} != {_TEMPLATE_AGENTS}")
test("at least 6 workers shipped", len(copied) >= 6)

mem = _install.CLAUDE_MEMORY.read_text(encoding="utf-8")
test("CLAUDE.md created", _install.CLAUDE_MEMORY.is_file())
test("policy START marker injected", _install._ORCH_POLICY_MARKER_START in mem)
test("policy END marker injected", _install._ORCH_POLICY_MARKER_END in mem)
test("single START marker occurrence", mem.count(_install._ORCH_POLICY_MARKER_START) == 1)


# ── 3. Idempotency ───────────────────────────────────────────────────────────

print("\n\U0001f501 deploy_claude_agents — idempotent re-run")

_install.deploy_claude_agents()
mem2 = _install.CLAUDE_MEMORY.read_text(encoding="utf-8")
test("CLAUDE.md unchanged on second run", mem == mem2)
test("no duplicate policy block", mem2.count(_install._ORCH_POLICY_MARKER_START) == 1)
copied2 = sorted(p.name for p in _install.CLAUDE_AGENTS_DIR.glob("*.md"))
test("agent set unchanged on second run", copied2 == copied)


# ── 4. Pre-existing CLAUDE.md content preserved ──────────────────────────────

print("\n\U0001f4dd deploy_claude_agents — preserves existing CLAUDE.md content")

home2 = SCRATCH / "home-existing"
if home2.exists():
    import shutil as _sh2

    _sh2.rmtree(home2, ignore_errors=True)
(home2 / ".claude").mkdir(parents=True, exist_ok=True)
_redirect_claude_home(home2)

user_content = "# My Claude Memory\n\nImportant personal notes I wrote.\n"
_install.CLAUDE_MEMORY.write_text(user_content, encoding="utf-8")

_install.deploy_claude_agents()
final = _install.CLAUDE_MEMORY.read_text(encoding="utf-8")
test("user content preserved", "Important personal notes I wrote." in final)
test(
    "policy injected after user content",
    final.index("personal notes") < final.index(_install._ORCH_POLICY_MARKER_START),
)


# ── 5. Out-of-date policy block replaced in place ────────────────────────────

print("\n\u267b\ufe0f  deploy_claude_agents — replaces stale policy in place")

stale = (
    "# Header\n\n"
    + _install._ORCH_POLICY_MARKER_START
    + "\nOLD STALE POLICY TEXT\n"
    + _install._ORCH_POLICY_MARKER_END
    + "\n\n# Footer\n"
)
_install.CLAUDE_MEMORY.write_text(stale, encoding="utf-8")
_install.deploy_claude_agents()
replaced = _install.CLAUDE_MEMORY.read_text(encoding="utf-8")
test("stale policy text removed", "OLD STALE POLICY TEXT" not in replaced)
test("header preserved on replace", replaced.startswith("# Header"))
test("footer preserved on replace", replaced.rstrip().endswith("# Footer"))
test("single marker after replace", replaced.count(_install._ORCH_POLICY_MARKER_START) == 1)


# ── Restore + Summary ────────────────────────────────────────────────────────

_install.CLAUDE_DIR = _orig_dir
_install.CLAUDE_AGENTS_DIR = _orig_agents
_install.CLAUDE_MEMORY = _orig_memory

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed")

import shutil

try:
    shutil.rmtree(SCRATCH, ignore_errors=True)
except Exception:
    pass

sys.exit(1 if FAIL else 0)
