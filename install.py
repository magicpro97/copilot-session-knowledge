#!/usr/bin/env python3
"""
install.py — Smart installer for session knowledge tools

Usage:
    python install.py                        # Auto-detect and show status
    python install.py --deploy-skill         # Deploy SKILL.md to current project
    python install.py --deploy-hooks         # Deploy hooks.json to ~/.copilot/hooks/
    python install.py --deploy-instructions  # Deploy global instructions to ~/.github/
    python install.py --inject-global        # Add session-knowledge to global copilot-instructions
    python install.py --install-git-hooks    # Install pre-commit/pre-push into current repo's .git/hooks/
    python install.py --lock-hooks           # Lock hooks with OS immutable flags (tamper protection)
    python install.py --unlock-hooks         # Unlock hooks for updates
    python install.py --test                 # Run self-test
    python install.py --uninstall            # Remove installed files
    python install.py --help                 # Show this help

Tamper Protection:
    --lock-hooks sets OS-level immutable flags on all hook scripts + hooks.json:
      macOS: chflags uchg (user immutable, no sudo needed)
      Linux: chattr +i (requires sudo)
      Windows: attrib +R (read-only, weaker)
    Also generates SHA256 manifest checked by verify-integrity.py at session start.
"""

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Windows console encoding fix (same pattern as other tools)
# ---------------------------------------------------------------------------
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Atomic write helper (P1-5, P1-7)
# ---------------------------------------------------------------------------
def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write content to path atomically via temp + os.replace. No CRLF translation."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content.encode(encoding))
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HOME = Path.home()


def _real_home():
    """Get real user home, even under sudo (where Path.home() returns /root)."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and os.name != "nt":
        import pwd
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return HOME


# Host metadata is centralised in host_manifest.py — import canonical constants.
# Do NOT add new hosts here; update host_manifest.py through the review process.
from host_manifest import (  # noqa: E402
    COPILOT_DIR,
    CLAUDE_DIR,
    HOST_DIRS as KNOWN_HOSTS,
    HOST_SKILL_SUBPATHS,
)

TOOLS_DIR = COPILOT_DIR / "tools"
SESSION_STATE = COPILOT_DIR / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"
SKILLS_SRC = COPILOT_DIR / "skills" / "session-knowledge" / "SKILL.md"
GLOBAL_INSTRUCTIONS = HOME / ".github" / "copilot-instructions.md"
LOCK_FILE = SESSION_STATE / ".watcher.lock"

# Registry of projects that have received a skill deployment via install.py
# --deploy-skill or setup-project.py.  auto-update-tools.py reads this so
# vendored-skill updates propagate to every registered project even when
# auto-update runs from the tools repo or a non-project context (e.g. launchd).
REGISTRY_PATH = SESSION_STATE / "tools-managed-projects.json"


def _load_project_registry() -> list[str]:
    """Return the list of registered project root paths (strings)."""
    try:
        if REGISTRY_PATH.exists():
            data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            return [p for p in data.get("projects", []) if isinstance(p, str)]
    except Exception:
        pass
    return []


def _register_project(project_root: Path) -> None:
    """Add *project_root* to the persistent registry (idempotent, silent on error)."""
    try:
        projects = _load_project_registry()
        key = str(project_root.resolve())
        if key not in projects:
            projects.append(key)
            REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
            # P1-7: atomic write prevents registry truncation on concurrent access
            _atomic_write_text(REGISTRY_PATH, json.dumps({"projects": projects}, indent=2))
    except Exception:
        pass


# Resolve the repo's templates/ directory (works when run from repo or ~/.copilot/tools/)
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_SKILL_MD = _SCRIPT_DIR / "templates" / "SKILL.md"

TOOL_FILES = [
    "build-session-index.py",
    "extract-knowledge.py",
    "query-session.py",
    "briefing.py",
    "watch-sessions.py",
    "learn.py",
    "embed.py",
    "claude-adapter.py",
    "sync-knowledge.py",
    "sync-config.py",
    "sync-daemon.py",
    "sync-status.py",
    "sync-gateway.py",
    "generate-summary.py",
    "install.py",
]

SUPPORT_FILES = [
    "README.md",
    "KNOWLEDGE.md",
    "embedding-config.json",
]

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------
OK = "\u2713"   # ✓
FAIL = "\u2717" # ✗
INFO = "\u2139" # ℹ
WARN = "\u26a0"  # ⚠


# ===================================================================
# Helpers
# ===================================================================

def _tilde(p: Path) -> str:
    """Show path relative to ~ for readability."""
    try:
        return "~/" + p.relative_to(HOME).as_posix()
    except ValueError:
        return str(p)


def _count_scripts(d: Path) -> int:
    """Count .py files in a directory."""
    if not d.is_dir():
        return 0
    return sum(1 for f in d.iterdir() if f.suffix == ".py")


def _db_counts() -> dict:
    """Read document / knowledge-entry / relation counts from the DB."""
    result = {"documents": 0, "entries": 0, "relations": 0, "sessions": 0}
    if not DB_PATH.is_file():
        return result
    try:
        db = sqlite3.connect(str(DB_PATH))
        _ALLOWED_TABLES = {"documents", "knowledge_entries", "knowledge_relations", "sessions"}
        for table, key in [
            ("documents", "documents"),
            ("knowledge_entries", "entries"),
            ("knowledge_relations", "relations"),
            ("sessions", "sessions"),
        ]:
            assert table in _ALLOWED_TABLES, f"Unexpected table: {table}"
            try:
                result[key] = db.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass
        db.close()
    except Exception:
        pass
    return result


def _watcher_running() -> bool:
    """Check if the session watcher is currently running."""
    if not LOCK_FILE.is_file():
        return False
    try:
        raw = LOCK_FILE.read_text(encoding="utf-8").strip()
        # Handle both formats: bare PID ("9119") and JSON ({"pid": 9119})
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return False
        if isinstance(data, int):
            pid = data
        elif isinstance(data, dict):
            pid = data.get("pid")
        else:
            return False
        if pid is None:
            return False
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x100000, False, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(int(pid), 0)
            return True
    except (OSError, ValueError, PermissionError, TypeError):
        return False


def _git_root() -> "Path | None":
    """Find the git root from cwd."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return Path(r.stdout.strip())
    except Exception:
        pass
    return None


def _fts_working() -> bool:
    """Quick FTS5 probe."""
    if not DB_PATH.is_file():
        return False
    try:
        db = sqlite3.connect(str(DB_PATH))
        rows = db.execute(
            "SELECT COUNT(*) FROM knowledge_fts "
            "WHERE knowledge_fts MATCH 'test OR error'"
        ).fetchone()
        db.close()
        return rows[0] >= 0
    except Exception:
        return False


# ===================================================================
# 1. Detection / Status
# ===================================================================

def show_status() -> bool:
    """Print agent detection table. Returns True if tools are installed."""
    print("\nAgent Detection:")

    # Iterate over KNOWN_HOSTS to keep Copilot CLI + Claude Code symmetrical.
    for host_name, host_dir in KNOWN_HOSTS.items():
        if host_dir.is_dir():
            print(f"  {OK} {host_name}: {_tilde(host_dir)} found")
        else:
            print(f"  {FAIL} {host_name}: {_tilde(host_dir)} not found")

    # Tools directory
    n_scripts = _count_scripts(TOOLS_DIR)
    if TOOLS_DIR.is_dir() and n_scripts > 0:
        print(f"  {OK} Tools dir:   {_tilde(TOOLS_DIR)} ({n_scripts} scripts)")
        installed = True
    else:
        print(f"  {FAIL} Tools dir:   {_tilde(TOOLS_DIR)} not found")
        installed = False

    # Session data
    if SESSION_STATE.is_dir():
        counts = _db_counts()
        sessions = counts["sessions"]
        if sessions > 0:
            print(f"  {OK} Session data: {sessions} sessions indexed")
        else:
            n_dirs = sum(
                1 for d in SESSION_STATE.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            )
            if n_dirs:
                print(
                    f"  {OK} Session data: {n_dirs} session dirs "
                    f"(not yet indexed)"
                )
            else:
                print(f"  {FAIL} Session data: empty")
    else:
        print(f"  {FAIL} Session data: {_tilde(SESSION_STATE)} not found")

    # Knowledge DB
    if DB_PATH.is_file():
        counts = _db_counts()
        print(
            f"  {OK} Knowledge DB: {counts['entries']} entries, "
            f"{counts['relations']} relations"
        )
    else:
        print(f"  {FAIL} Knowledge DB: not built")

    # Watcher
    if _watcher_running():
        print(f"  {OK} Watcher:     running")
    else:
        print(f"  {FAIL} Watcher:     not running")

    return installed


# ===================================================================
# 2. Skill Deployment
# ===================================================================

MINIMAL_SKILL_MD = textwrap.dedent("""\
    ---
    name: session-knowledge
    description: >-
      Search past Copilot/Claude session knowledge before complex tasks. Run briefing.py
      for relevant mistakes, patterns, decisions. Use query-session.py to search errors,
      tools, architecture choices. Supports semantic search with embeddings.
    ---

    # Session Knowledge Tools — Agent Skill

    Before starting any task, query the shared knowledge base for relevant past
    experience, patterns, and known mistakes.

    ## Available Tools

    ```bash
    # Search the knowledge base (Windows: python instead of python3)
    python3 ~/.copilot/tools/query-session.py "search terms"

    # Get a context briefing for your current task
    python3 ~/.copilot/tools/briefing.py "task description"

    # Manual compatibility path for ad hoc sub-agent prompts
    python3 ~/.copilot/tools/briefing.py "task description" --for-subagent

    # Preferred delegated-agent path (tentacle structured evidence)
    python3 ~/.copilot/tools/tentacle.py swarm <name> --briefing

    # Record new learnings
    python3 ~/.copilot/tools/learn.py --mistake "Title" "What went wrong and fix"
    python3 ~/.copilot/tools/learn.py --pattern "Title" "What works well"
    python3 ~/.copilot/tools/learn.py --decision "Title" "Choice and rationale"

    # Show past mistakes and patterns
    python3 ~/.copilot/tools/query-session.py --mistakes
    python3 ~/.copilot/tools/query-session.py --patterns
    python3 ~/.copilot/tools/query-session.py --decisions
    ```

    ## Workflow

    1. **Before starting**: run `briefing.py` with a description of your task.
    2. **During work**: if you hit an issue, search for similar past problems.
    3. **After finishing**: record any new mistakes, patterns, or decisions
       with `learn.py`.
    4. **Delegated agents (preferred)**: use `tentacle.py ... --briefing`
       so dispatch injects bounded `[KNOWLEDGE EVIDENCE]` from task recall first.
    5. **Manual compatibility**: for ad hoc prompts, run
       `briefing.py --for-subagent` and inject output directly.
""")


def deploy_skill():
    """Deploy SKILL.md to the current project directory."""
    project_root = _git_root() or Path.cwd()
    print(f"\nSkill Deployment")
    print(f"  Project: {project_root}")

    # Read source skill content (priority: installed > repo template > minimal)
    if SKILLS_SRC.is_file():
        skill_content = SKILLS_SRC.read_text(encoding="utf-8")
        print(f"  {OK} Source: {_tilde(SKILLS_SRC)}")
    elif _REPO_SKILL_MD.is_file():
        skill_content = _REPO_SKILL_MD.read_text(encoding="utf-8")
        print(f"  {OK} Source: {_REPO_SKILL_MD}")
    else:
        skill_content = MINIMAL_SKILL_MD
        print(
            f"  {INFO} Source: generating minimal SKILL.md "
            f"(no source template found)"
        )

    deployed = []

    # Iterate manifest-defined hosts so adding a new host only requires
    # updating host_manifest.py — no changes needed here.
    for host_name, host_dir in KNOWN_HOSTS.items():
        if not host_dir.is_dir():
            continue
        subpath = HOST_SKILL_SUBPATHS.get(host_name)
        if subpath is None:
            continue
        target = project_root / subpath
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, skill_content)  # P1-5: atomic write
        deployed.append((host_name, target))
        print(f"  {OK} {host_name}: {target.relative_to(project_root)}")

    if not deployed:
        print(f"  {FAIL} No agents detected — nothing deployed")
        print(f"      Create ~/.copilot/ or ~/.claude/ first.")
        return

    print(f"\n  Deployed {len(deployed)} skill file(s).")

    # Register this project so auto-update-tools.py can propagate vendored-skill
    # updates even when running from the tools repo / launchd / shell auto-start.
    _register_project(project_root)


# ===================================================================
# 2b. Global Instructions Injection
# ===================================================================

_INJECT_MARKER_START = "<!-- SESSION-KNOWLEDGE-START -->"
_INJECT_MARKER_END = "<!-- SESSION-KNOWLEDGE-END -->"

GLOBAL_INJECT_BLOCK = textwrap.dedent(f"""\
{_INJECT_MARKER_START}
## 🧠 Session Knowledge

> Full briefing strategy and escalation rules are in the always-loaded
> `~/.github/instructions/session-knowledge.instructions.md`.
> Follow the progressive escalation model defined there — start minimal,
> escalate only when compact output reveals a relevant hit.

Quick reference:

```bash
# Before moderate/complex tasks — start here, escalate only if needed
python3 ~/.copilot/tools/briefing.py --auto --compact

# For delegated tentacle agents — preferred structured recall path
python3 ~/.copilot/tools/tentacle.py swarm <name> --briefing

# Manual compatibility for ad hoc sub-agent prompts
python3 ~/.copilot/tools/briefing.py "task description" --for-subagent

# After resolving a non-trivial issue — record the learning
python3 ~/.copilot/tools/learn.py --mistake "Title" "Root cause and fix"
python3 ~/.copilot/tools/learn.py --pattern "Title" "What works well"
```
{_INJECT_MARKER_END}
""")


_TEMPLATES_DIR = _SCRIPT_DIR / "templates"
_INSTRUCTIONS_TEMPLATES = _TEMPLATES_DIR / "instructions"


def deploy_hooks():
    """Deploy hooks.json and Python hook scripts to ~/.copilot/hooks/.

    Hook deployment is Copilot CLI-only.  Claude Code configures hooks via
    ~/.claude/settings.json, which uses a different format not managed here.
    """
    print("\nDeploy Hooks (Copilot CLI)")

    hooks_src = _SCRIPT_DIR / "hooks" / "hooks.json"
    if not hooks_src.is_file():
        # Backward-compatible fallback for older repos.
        hooks_src = _SCRIPT_DIR / ".github" / "hooks" / "hooks.json"
    hooks_dst_dir = COPILOT_DIR / "hooks"
    hooks_dst = hooks_dst_dir / "hooks.json"

    if not hooks_src.is_file():
        print(f"  {FAIL} Source hooks.json not found: {_tilde(hooks_src)}")
        return

    hooks_dst_dir.mkdir(parents=True, exist_ok=True)

    # Deploy hooks.json
    new = hooks_src.read_text(encoding="utf-8")
    if hooks_dst.is_file():
        old = hooks_dst.read_text(encoding="utf-8")
        if old == new:
            print(f"  {INFO} hooks.json — already up to date")
        else:
            backup = hooks_dst.with_suffix(".json.backup")
            shutil.copy2(str(hooks_dst), str(backup))
            _atomic_write_text(hooks_dst, new)  # P1-5: atomic write
            print(f"  {OK} hooks.json — updated (backup: {backup.name})")
    else:
        _atomic_write_text(hooks_dst, new)  # P1-5: atomic write
        print(f"  {OK} hooks.json — created")

    # Ensure markers directory exists
    markers_dir = COPILOT_DIR / "markers"
    markers_dir.mkdir(parents=True, exist_ok=True)
    print(f"  {OK} markers/ directory ready")

    # List available hooks (root-level + any subdirectories, e.g. hooks/rules/)
    hooks_dir = _SCRIPT_DIR / "hooks"
    py_hooks: list[Path] = sorted(hooks_dir.glob("*.py")) if hooks_dir.is_dir() else []
    if hooks_dir.is_dir():
        for sub in sorted(hooks_dir.iterdir()):
            if sub.is_dir() and not sub.name.startswith((".", "_")):
                py_hooks += sorted(sub.glob("*.py"))
    print(f"\n  {len(py_hooks)} Python hook scripts available:")
    for h in py_hooks:
        rel = h.relative_to(hooks_dir)
        print(f"    • {rel.as_posix()}")




def deploy_instructions():
    """Deploy global copilot-instructions.md and scope-specific instruction files."""
    print("\nDeploy Global Instructions")

    github_dir = HOME / ".github"
    instructions_dir = github_dir / "instructions"
    github_dir.mkdir(parents=True, exist_ok=True)
    instructions_dir.mkdir(parents=True, exist_ok=True)

    deployed = 0

    # 1. Core: copilot-instructions.md
    src = _TEMPLATES_DIR / "copilot-instructions.md"
    dst = GLOBAL_INSTRUCTIONS
    if src.is_file():
        if dst.is_file():
            old = dst.read_text(encoding="utf-8")
            new = src.read_text(encoding="utf-8")
            if old == new:
                print(f"  {INFO} copilot-instructions.md — already up to date")
            else:
                backup = dst.with_suffix(".md.backup")
                shutil.copy2(str(dst), str(backup))
                dst.write_text(new, encoding="utf-8")
                print(f"  {OK} copilot-instructions.md — updated (backup: {backup.name})")
                deployed += 1
        else:
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  {OK} copilot-instructions.md — created")
            deployed += 1
    else:
        print(f"  {FAIL} Template not found: {_tilde(src)}")

    # 2. Scope-specific instructions
    if _INSTRUCTIONS_TEMPLATES.is_dir():
        for src_file in sorted(_INSTRUCTIONS_TEMPLATES.glob("*.instructions.md")):
            dst_file = instructions_dir / src_file.name
            if dst_file.is_file():
                old = dst_file.read_text(encoding="utf-8")
                new = src_file.read_text(encoding="utf-8")
                if old == new:
                    print(f"  {INFO} {src_file.name} — already up to date")
                else:
                    dst_file.write_text(new, encoding="utf-8")
                    print(f"  {OK} {src_file.name} — updated")
                    deployed += 1
            else:
                dst_file.write_text(src_file.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"  {OK} {src_file.name} — created")
                deployed += 1

    # 3. session-knowledge.instructions.md (from templates root)
    sk_src = _TEMPLATES_DIR / "session-knowledge.instructions.md"
    sk_dst = instructions_dir / "session-knowledge.instructions.md"
    if sk_src.is_file():
        if sk_dst.is_file():
            if sk_dst.read_text(encoding="utf-8") == sk_src.read_text(encoding="utf-8"):
                print(f"  {INFO} session-knowledge.instructions.md — already up to date")
            else:
                sk_dst.write_text(sk_src.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"  {OK} session-knowledge.instructions.md — updated")
                deployed += 1
        else:
            sk_dst.write_text(sk_src.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  {OK} session-knowledge.instructions.md — created")
            deployed += 1

    print(f"\n  Deployed {deployed} file(s) to {_tilde(github_dir)}")


def inject_global():
    """Inject session-knowledge section into global copilot-instructions.md."""
    print("\nGlobal Instructions Injection")
    print(f"  Target: {_tilde(GLOBAL_INSTRUCTIONS)}")

    # Warn if the canonical instructions target the pointer block references is missing.
    _sk_instructions = HOME / ".github" / "instructions" / "session-knowledge.instructions.md"
    if not _sk_instructions.is_file():
        print(
            f"  {WARN} session-knowledge.instructions.md not found at "
            f"{_tilde(_sk_instructions)}.\n"
            f"        Run `python3 install.py --deploy-instructions` first so the pointer "
            f"block does not reference a missing file."
        )

    # Ensure ~/.github/ exists
    GLOBAL_INSTRUCTIONS.parent.mkdir(parents=True, exist_ok=True)

    if GLOBAL_INSTRUCTIONS.is_file():
        content = GLOBAL_INSTRUCTIONS.read_text(encoding="utf-8")

        # Check if already injected
        if _INJECT_MARKER_START in content:
            # Replace existing block
            import re
            pattern = re.escape(_INJECT_MARKER_START) + r".*?" + re.escape(_INJECT_MARKER_END)
            new_content = re.sub(pattern, GLOBAL_INJECT_BLOCK.strip(), content, flags=re.DOTALL)
            if new_content != content:
                GLOBAL_INSTRUCTIONS.write_text(new_content, encoding="utf-8")
                print(f"  {OK} Updated existing session-knowledge section")
            else:
                print(f"  {INFO} Already up to date")
            return

        # Find insertion point: after the mandatory section header
        # Look for the numbered list in "BẮT BUỘC" section and insert after it
        lines = content.split("\n")
        insert_idx = None

        # Strategy: insert after the "KHÔNG ĐƯỢC:" block ends (first blank line after it)
        in_mandatory = False
        found_khong_duoc = False
        for i, line in enumerate(lines):
            if "BẮT BUỘC" in line:
                in_mandatory = True
            if in_mandatory and "KHÔNG ĐƯỢC" in line:
                found_khong_duoc = True
            if found_khong_duoc and line.strip() == "" and i > 0 and lines[i - 1].strip().startswith("- "):
                insert_idx = i + 1
                break

        if insert_idx is None:
            # Fallback: insert after the "---" separator or at position 2 (after title)
            for i, line in enumerate(lines):
                if line.strip() == "---" and i > 5:
                    insert_idx = i
                    break
            if insert_idx is None:
                insert_idx = 2  # After title

        lines.insert(insert_idx, "\n" + GLOBAL_INJECT_BLOCK)
        GLOBAL_INSTRUCTIONS.write_text("\n".join(lines), encoding="utf-8")
        print(f"  {OK} Injected session-knowledge section at line {insert_idx}")
    else:
        # Create new file with just the injection block
        header = "# Global Copilot Instructions\n\n"
        GLOBAL_INSTRUCTIONS.write_text(header + GLOBAL_INJECT_BLOCK, encoding="utf-8")
        print(f"  {OK} Created {_tilde(GLOBAL_INSTRUCTIONS)} with session-knowledge section")

    print(f"  {INFO} Injected pointer block — full policy lives in session-knowledge.instructions.md")


# ===================================================================
# 3. Self-Test
# ===================================================================

def run_self_test():
    """Import each tool module and verify the knowledge base."""
    print("\nSelf-Test Results:")

    tool_scripts = [f for f in TOOL_FILES if f != "install.py"]
    pass_count = 0
    fail_count = 0

    for filename in tool_scripts:
        filepath = TOOLS_DIR / filename
        if not filepath.is_file():
            print(f"  {FAIL} {filename} \u2014 not found")
            fail_count += 1
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                filename.replace("-", "_").replace(".py", ""),
                str(filepath),
            )
            if spec and spec.loader:
                with open(filepath, "r", encoding="utf-8") as fh:
                    compile(fh.read(), str(filepath), "exec")
                print(f"  {OK} {filename} \u2014 importable")
                pass_count += 1
            else:
                print(f"  {FAIL} {filename} \u2014 spec creation failed")
                fail_count += 1
        except SyntaxError as e:
            print(
                f"  {FAIL} {filename} \u2014 SyntaxError: "
                f"{e.msg} (line {e.lineno})"
            )
            fail_count += 1
        except Exception as e:
            print(f"  {FAIL} {filename} \u2014 {type(e).__name__}: {e}")
            fail_count += 1

    # Knowledge DB
    if DB_PATH.is_file():
        try:
            counts = _db_counts()
            print(f"  {OK} Knowledge DB \u2014 {counts['entries']} entries")
            pass_count += 1

            if _fts_working():
                print(f"  {OK} FTS index \u2014 working")
                pass_count += 1
            else:
                print(f"  {FAIL} FTS index \u2014 query failed")
                fail_count += 1
        except Exception as e:
            print(f"  {FAIL} Knowledge DB \u2014 {e}")
            fail_count += 1
    else:
        print(f"  {FAIL} Knowledge DB \u2014 not found at {_tilde(DB_PATH)}")
        fail_count += 1
        print(f"  {FAIL} FTS index \u2014 no DB")
        fail_count += 1

    # Watcher
    if _watcher_running():
        print(f"  {OK} Watcher \u2014 running")
        pass_count += 1
    else:
        print(f"  {FAIL} Watcher \u2014 not running")
        fail_count += 1

    total = pass_count + fail_count
    print(f"\n  {pass_count}/{total} checks passed", end="")
    if fail_count:
        print(f" ({fail_count} failed)")
    else:
        print(" \u2014 all good!")


# ===================================================================
# 4. Uninstall
# ===================================================================

def uninstall():
    """Remove installed tools. Preserves session-state data."""
    print("\nUninstall \u2014 Session Knowledge Tools")
    print("=" * 50)

    removable: list[Path] = []

    for f in TOOL_FILES + SUPPORT_FILES:
        p = TOOLS_DIR / f
        if p.is_file():
            removable.append(p)

    pycache = TOOLS_DIR / "__pycache__"
    if pycache.is_dir():
        removable.append(pycache)

    watch_state = SESSION_STATE / ".watch-state.json"
    if watch_state.is_file():
        removable.append(watch_state)
    if LOCK_FILE.is_file():
        removable.append(LOCK_FILE)

    if not removable:
        print(f"\n  Nothing to remove.")
        return

    print(f"\n  Files to remove:")
    for p in removable:
        label = "dir " if p.is_dir() else ""
        print(f"    {label}{_tilde(p)}")

    print(f"\n  Preserved (your data):")
    print(f"    {_tilde(SESSION_STATE)}  (session data)")
    if DB_PATH.is_file():
        print(f"    {_tilde(DB_PATH)}  (knowledge database)")

    print()
    try:
        answer = input("  Proceed with uninstall? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        return

    if answer not in ("y", "yes"):
        print("  Cancelled.")
        return

    removed = 0
    for p in removable:
        try:
            if p.is_dir():
                shutil.rmtree(str(p))
            else:
                p.unlink()
            print(f"  {OK} Removed {_tilde(p)}")
            removed += 1
        except Exception as e:
            print(f"  {FAIL} Could not remove {_tilde(p)}: {e}")

    if TOOLS_DIR.is_dir():
        remaining = list(TOOLS_DIR.iterdir())
        if not remaining:
            try:
                TOOLS_DIR.rmdir()
                print(f"  {OK} Removed empty {_tilde(TOOLS_DIR)}")
            except Exception:
                pass

    print(f"\n  Uninstall complete \u2014 removed {removed} item(s).")
    print(f"  Session data preserved at {_tilde(SESSION_STATE)}")


# ===================================================================
# 5. Install / First-Run
# ===================================================================

def install():
    """Copy tools into place and build the initial index."""
    print(f"\nInstalling Session Knowledge Tools...")
    print(f"  Target: {_tilde(TOOLS_DIR)}")

    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  {OK} Tools directory ready")

    source_dir = Path(__file__).resolve().parent
    if source_dir.resolve() != TOOLS_DIR.resolve():
        copied = 0
        for f in TOOL_FILES + SUPPORT_FILES:
            src = source_dir / f
            dst = TOOLS_DIR / f
            if src.is_file():
                shutil.copy2(str(src), str(dst))
                copied += 1
        print(f"  {OK} Copied {copied} files")
    else:
        print(f"  {OK} Scripts already in place")

    print(f"\n  Building knowledge index...")
    if SESSION_STATE.is_dir():
        indexer = TOOLS_DIR / "build-session-index.py"
        if indexer.is_file():
            result = subprocess.run(
                [sys.executable, str(indexer)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    lo = line.lower()
                    if any(k in lo for k in [
                        "indexed", "sessions:", "documents:", "fts", "total",
                    ]):
                        print(f"    {line.strip()}")
                print(f"  {OK} Index built")
            else:
                print(f"  {FAIL} Indexer error: {result.stderr[:200]}")

        extractor = TOOLS_DIR / "extract-knowledge.py"
        if extractor.is_file():
            print(f"\n  Extracting knowledge...")
            result = subprocess.run(
                [sys.executable, str(extractor)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    lo = line.lower()
                    if any(k in lo for k in [
                        "extracted", "total", "category", "entries",
                    ]):
                        print(f"    {line.strip()}")
                print(f"  {OK} Knowledge extracted")
    else:
        print(
            f"  {INFO} No session-state yet \u2014 "
            f"index will be built on first use"
        )

    print(f"\n{'=' * 50}")
    print(f"  Installation complete!")
    print(f"{'=' * 50}")
    _show_usage_hints()


def _show_usage_hints():
    """Print helpful next-step commands."""
    qs = _tilde(TOOLS_DIR / "query-session.py")
    br = _tilde(TOOLS_DIR / "briefing.py")
    ws = _tilde(TOOLS_DIR / "watch-sessions.py")
    inst = _tilde(TOOLS_DIR / "install.py")
    print(f"\n  Quick start:")
    print(f"    python {qs} \"search terms\"   # Search knowledge base")
    print(f"    python {br} \"your task\"       # Context briefing")
    print(f"    python {ws}                    # Start watcher daemon")
    print(f"\n  Management:")
    print(f"    python {inst} --deploy-skill          # Add skill to project")
    print(f"    python {inst} --deploy-hooks           # Deploy hooks")
    print(f"    python {inst} --deploy-instructions   # Deploy global instructions")
    print(f"    python {inst} --inject-global         # Add to global copilot-instructions")
    print(f"    python {inst} --install-git-hooks     # Install pre-commit/pre-push git hooks")
    print(f"    python {inst} --lock-hooks             # Lock hooks (tamper protection)")
    print(f"    python {inst} --unlock-hooks           # Unlock hooks for updates")
    print(f"    python {inst} --test                  # Run self-test")
    print(f"    python {inst} --uninstall             # Remove tools")
    print(f"\n  Sync rollout note:")
    print("    sync-config.py --setup expects an HTTP(S) gateway URL (not raw Postgres/libSQL DSN)")
    print("    Default provider rollout recommendation: Neon (Postgres) + Railway (thin gateway host)")
    print(f"\n  Trend Scout automation note:")
    print("    Use trend-scout.py / trend-scout.yml for scheduled scouting; do not wire it to preToolUse/postToolUse hooks")


# ===================================================================
# Main
# ===================================================================


def lock_hooks():
    """Lock hook files with OS-level immutable flags + SHA256 manifest.
    
    macOS: chflags schg (system immutable — requires sudo)
    Linux: chattr +i (needs sudo/root)
    Windows: attrib +R
    Also: generates HMAC secret, sanitizes config.json, locks config.json.
    """
    import hashlib
    import platform
    import secrets

    hooks_dir = _SCRIPT_DIR / "hooks"
    real_home = _real_home()
    hooks_dst_dir = real_home / ".copilot" / "hooks"
    hooks_dst = hooks_dst_dir / "hooks.json"
    config_json = real_home / ".copilot" / "config.json"
    secret_path = hooks_dst_dir / ".marker-secret"

    print("\n🔒 Lock Hooks — Tamper Protection")

    # Collect all hook files to protect (including rules/ subdirectory)
    hook_files = sorted(hooks_dir.glob("*.py")) if hooks_dir.is_dir() else []
    rules_dir = hooks_dir / "rules"
    if rules_dir.is_dir():
        hook_files += sorted(rules_dir.glob("*.py"))
    if not hook_files:
        print(f"  {FAIL} No hook scripts found in {_tilde(hooks_dir)}")
        return

    # 0. Generate HMAC secret if not exists
    hooks_dst_dir.mkdir(parents=True, exist_ok=True)
    if not secret_path.is_file():
        secret_path.write_text(secrets.token_hex(32), encoding="utf-8")
        print(f"  {OK} HMAC secret generated: {_tilde(secret_path)}")
    else:
        print(f"  {OK} HMAC secret exists: {_tilde(secret_path)}")

    # 0b. Sanitize config.json — remove disableAllHooks
    if config_json.is_file():
        try:
            cfg = json.loads(config_json.read_text(encoding="utf-8"))
            if "disableAllHooks" in cfg:
                del cfg["disableAllHooks"]
                config_json.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
                print(f"  {WARN} Removed disableAllHooks from config.json!")
            else:
                print(f"  {OK} config.json clean (no disableAllHooks)")
        except Exception as e:
            print(f"  {WARN} Could not check config.json: {e}")

    # 0c. Clear tamper marker since we're re-locking
    tamper_marker = real_home / ".copilot" / "markers" / "hooks-tampered"
    if tamper_marker.is_file():
        try:
            tamper_marker.unlink()
            print(f"  {OK} Cleared hooks-tampered kill-switch")
        except Exception:
            pass

    # 1. Generate SHA256 manifest
    manifest = {"files": {}, "hooks_json": None}
    for hf in hook_files:
        h = hashlib.sha256(hf.read_bytes()).hexdigest()
        # Use relative path for subdirectory files (e.g., rules/briefing.py)
        rel_name = hf.relative_to(hooks_dir).as_posix()
        manifest["files"][rel_name] = h
        print(f"  {OK} {rel_name}: {h[:16]}...")

    if hooks_dst.is_file():
        h = hashlib.sha256(hooks_dst.read_bytes()).hexdigest()
        manifest["hooks_json"] = h
        print(f"  {OK} hooks.json: {h[:16]}...")

    # Save manifest
    manifest_path = hooks_dst_dir / "integrity-manifest.json"
    hooks_dst_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n  {OK} Manifest saved: {_tilde(manifest_path)}")

    # 2. Set OS-level immutable flags
    system = platform.system()
    protected = 0

    files_to_lock = list(hook_files) + [hooks_dst, manifest_path, secret_path, config_json]

    if system == "Darwin":
        # schg = system immutable — requires sudo to set, cannot be removed without sudo
        # Much stronger than uchg (user immutable) which same user can remove
        is_root = os.geteuid() == 0
        flag = "schg"
        for f in files_to_lock:
            if f.is_file():
                cmd = ["chflags", flag, str(f)] if is_root else ["sudo", "chflags", flag, str(f)]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    protected += 1
                else:
                    err = result.stderr.strip() or "unknown error"
                    print(f"  {FAIL} chflags {flag} failed: {f.name} ({err})")
        if protected:
            print(f"\n  {OK} {protected} files locked (chflags {flag} — system immutable)")
            print("  To unlock: sudo python3 install.py --unlock-hooks")
        else:
            print(f"\n  {WARN} chflags {flag} requires sudo. Run: sudo python3 install.py --lock-hooks")

    elif system == "Linux":
        # chattr +i requires root — detect if already root to avoid double-sudo
        is_root = os.geteuid() == 0
        chmod_fallback = 0
        for f in files_to_lock:
            if f.is_file():
                # WSL: files on /mnt/ (NTFS) don't support chattr — fallback to chmod
                on_ntfs = str(f).startswith("/mnt/")
                if on_ntfs:
                    try:
                        f.chmod(0o444)
                        chmod_fallback += 1
                    except OSError as e:
                        print(f"  {FAIL} chmod failed: {f.name} ({e})")
                else:
                    cmd = ["chattr", "+i", str(f)] if is_root else ["sudo", "chattr", "+i", str(f)]
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode == 0:
                        protected += 1
                    else:
                        err = result.stderr.strip() or "unknown error"
                        print(f"  {FAIL} chattr failed: {f.name} ({err})")
        if protected:
            print(f"\n  {OK} {protected} files locked (chattr +i)")
        if chmod_fallback:
            print(f"  {OK} {chmod_fallback} files set read-only (chmod 444 — WSL/NTFS fallback)")
        if not protected and not chmod_fallback:
            print(f"\n  {WARN} chattr requires root. Run: sudo python3 install.py --lock-hooks")

    elif system == "Windows":
        for f in files_to_lock:
            if f.is_file():
                result = subprocess.run(["attrib", "+R", str(f)],
                                       capture_output=True, text=True)
                if result.returncode == 0:
                    protected += 1
        print(f"\n  {OK} {protected} files set read-only (attrib +R)")
        print("  Note: attrib +R is weaker than Unix immutable flags")

    else:
        print(f"\n  {WARN} Unknown OS: {system}. Manual protection needed.")

    print(f"\n  Agent CANNOT modify hook files without unlocking first.")


def unlock_hooks():
    """Remove OS-level immutable flags from hook files, config.json, and secret."""
    import platform

    hooks_dir = _SCRIPT_DIR / "hooks"
    real_home = _real_home()
    hooks_dst_dir = real_home / ".copilot" / "hooks"
    hooks_dst = hooks_dst_dir / "hooks.json"
    manifest_path = hooks_dst_dir / "integrity-manifest.json"
    config_json = real_home / ".copilot" / "config.json"
    secret_path = hooks_dst_dir / ".marker-secret"

    print("\n🔓 Unlock Hooks")

    hook_files = sorted(hooks_dir.glob("*.py")) if hooks_dir.is_dir() else []
    rules_dir = hooks_dir / "rules"
    if rules_dir.is_dir():
        hook_files += sorted(rules_dir.glob("*.py"))
    files_to_unlock = list(hook_files) + [hooks_dst, manifest_path, secret_path, config_json]

    system = platform.system()
    unlocked = 0

    if system == "Darwin":
        is_root = os.geteuid() == 0
        for f in files_to_unlock:
            if f.is_file():
                # Try noschg first (system immutable), fallback to nouchg (user immutable)
                cmd = ["chflags", "noschg", str(f)] if is_root else ["sudo", "chflags", "noschg", str(f)]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    unlocked += 1
                else:
                    # Fallback: try nouchg for legacy locks
                    cmd2 = ["chflags", "nouchg", str(f)]
                    result2 = subprocess.run(cmd2, capture_output=True, text=True)
                    if result2.returncode == 0:
                        unlocked += 1
        print(f"  {OK} {unlocked} files unlocked")

    elif system == "Linux":
        is_root = os.geteuid() == 0
        for f in files_to_unlock:
            if f.is_file():
                on_ntfs = str(f).startswith("/mnt/")
                if on_ntfs:
                    try:
                        f.chmod(0o644)
                        unlocked += 1
                    except OSError:
                        pass
                else:
                    cmd = ["chattr", "-i", str(f)] if is_root else ["sudo", "chattr", "-i", str(f)]
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode == 0:
                        unlocked += 1
                    else:
                        err = result.stderr.strip() or "unknown error"
                        print(f"  {FAIL} chattr unlock failed: {f.name} ({err})")
        print(f"  {OK} {unlocked} files unlocked (chattr -i / chmod 644)")

    elif system == "Windows":
        for f in files_to_unlock:
            if f.is_file():
                result = subprocess.run(["attrib", "-R", str(f)],
                                       capture_output=True, text=True)
                if result.returncode == 0:
                    unlocked += 1
        print(f"  {OK} {unlocked} files unlocked (attrib -R)")

    print("  ⚠️  Re-lock after updates: python3 install.py --lock-hooks")


def install_git_hooks(target_dir: "Path | None" = None) -> None:
    """Install pre-commit and pre-push git hooks into a repository's .git/hooks/.

    Copies hooks/pre-commit and hooks/pre-push from the tools source tree into
    <target_dir>/.git/hooks/ (or the git root of cwd if target_dir is None).
    Makes both files executable (chmod +x on POSIX).  Does NOT overwrite existing
    hooks that differ without interactive confirmation.

    The installed hooks reference $HOME/.copilot/tools unconditionally so they
    work correctly in any repo, not just the tools repo itself.
    """
    print("\nInstall Git Hooks (pre-commit / pre-push)")

    hooks_src_dir = _SCRIPT_DIR / "hooks"
    hook_names = ["pre-commit", "pre-push"]

    if target_dir is None:
        target_dir = _git_root()
    if target_dir is None:
        print(f"  {FAIL} Not inside a git repository — run from a project directory.")
        return

    git_hooks_dir = target_dir / ".git" / "hooks"
    if not git_hooks_dir.is_dir():
        print(f"  {FAIL} .git/hooks/ not found at {git_hooks_dir}")
        return

    installed = []
    skipped = []

    for hook_name in hook_names:
        src = hooks_src_dir / hook_name
        dst = git_hooks_dir / hook_name

        if not src.is_file():
            print(f"  {FAIL} Source hook not found: {_tilde(src)}")
            continue

        src_text = src.read_text(encoding="utf-8")

        if dst.is_file():
            dst_text = dst.read_text(encoding="utf-8")
            if dst_text == src_text:
                print(f"  {INFO} {hook_name} — already up to date")
                skipped.append(hook_name)
                continue
            if not sys.stdin.isatty():
                print(
                    f"  {WARN} {hook_name} already exists and differs — "
                    "skipping (non-interactive). Back it up and re-run to overwrite."
                )
                skipped.append(hook_name)
                continue
            try:
                answer = input(
                    f"  {WARN} {hook_name} already exists and differs. Overwrite? [y/N] "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print(f"\n  Skipping {hook_name}.")
                skipped.append(hook_name)
                continue
            if answer not in ("y", "yes"):
                print(f"  Skipping {hook_name}.")
                skipped.append(hook_name)
                continue
            backup = dst.with_name(hook_name + ".backup")
            shutil.copy2(str(dst), str(backup))
            print(f"  {INFO} Backed up existing hook to {backup.name}")

        dst.write_text(src_text, encoding="utf-8")
        if os.name != "nt":
            dst.chmod(dst.stat().st_mode | 0o111)
        installed.append(hook_name)
        print(f"  {OK} {hook_name} installed → {_tilde(dst)}")

    if installed:
        try:
            r = subprocess.run(
                ["git", "-C", str(target_dir), "config", "core.hooksPath", ".git/hooks"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                print(f"  {OK} core.hooksPath confirmed as .git/hooks")
        except Exception:
            pass

    total = len(installed) + len(skipped)
    if not installed and not skipped:
        print(f"  {FAIL} Nothing installed.")
    else:
        print(f"\n  {len(installed)} installed, {len(skipped)} already up to date (of {total} hooks).")
        print("  Hooks block git commit/push when dispatched-subagent-active marker is fresh.")
        print("  NOTE: After each 'auto-update-tools.py' run, re-run --install-git-hooks here")
        print("        to pick up new hook logic (auto-update cannot do this for you safely).")


def _dispatch_healer(flag: str) -> None:
    """Dispatch to copilot-cli-healer.py for schedule management."""
    healer = _SCRIPT_DIR / "copilot-cli-healer.py"
    if not healer.exists():
        print(f"  {FAIL} copilot-cli-healer.py not found at {_tilde(healer)}")
        return
    import subprocess as _sp
    _sp.run([sys.executable, str(healer), flag])


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--install-healer" in args:
        _dispatch_healer("--install-schedule")
        return

    if "--uninstall-healer" in args:
        _dispatch_healer("--uninstall-schedule")
        return

    if "--deploy-skill" in args:
        deploy_skill()
        return

    if "--deploy-hooks" in args:
        deploy_hooks()
        return

    if "--install-git-hooks" in args:
        install_git_hooks()
        return

    if "--lock-hooks" in args:
        lock_hooks()
        return

    if "--unlock-hooks" in args:
        unlock_hooks()
        return

    if "--deploy-instructions" in args:
        deploy_instructions()
        return

    if "--inject-global" in args:
        inject_global()
        return

    if "--test" in args:
        run_self_test()
        return

    if "--uninstall" in args:
        uninstall()
        return

    # Default: show status, then install if needed or show hints
    installed = show_status()
    if not installed:
        print()
        install()
    else:
        print(f"\n  Tools are installed and ready.")
        _show_usage_hints()


if __name__ == "__main__":
    main()
