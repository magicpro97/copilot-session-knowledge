#!/usr/bin/env python3
"""
install.py — Smart installer for session knowledge tools

Usage:
    python install.py                    # Auto-detect and show status
    python install.py --deploy-skill     # Deploy SKILL.md to current project
    python install.py --test             # Run self-test
    python install.py --uninstall        # Remove installed files
    python install.py --help             # Show this help
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
# Paths
# ---------------------------------------------------------------------------
HOME = Path.home()

COPILOT_DIR = HOME / ".copilot"
CLAUDE_DIR = HOME / ".claude"
TOOLS_DIR = COPILOT_DIR / "tools"
SESSION_STATE = COPILOT_DIR / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"
SKILLS_SRC = COPILOT_DIR / "skills" / "session-knowledge" / "SKILL.md"
LOCK_FILE = SESSION_STATE / ".watcher.lock"

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
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        pid = data.get("pid")
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
    except (json.JSONDecodeError, OSError, ValueError, PermissionError):
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

    # Copilot CLI
    if COPILOT_DIR.is_dir():
        print(f"  {OK} Copilot CLI: {_tilde(COPILOT_DIR)} found")
    else:
        print(f"  {FAIL} Copilot CLI: {_tilde(COPILOT_DIR)} not found")

    # Claude Code
    if CLAUDE_DIR.is_dir():
        print(f"  {OK} Claude Code: {_tilde(CLAUDE_DIR)} found")
    else:
        print(f"  {FAIL} Claude Code: {_tilde(CLAUDE_DIR)} not found")

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
    # Session Knowledge Tools — Agent Skill

    Before starting any task, query the shared knowledge base for relevant past
    experience, patterns, and known mistakes.

    ## Available Tools

    ```bash
    # Search the knowledge base
    python ~/.copilot/tools/query-session.py "search terms"

    # Get a context briefing for your current task
    python ~/.copilot/tools/briefing.py "task description"

    # Record new learnings
    python ~/.copilot/tools/learn.py --mistake "Title" "What went wrong and fix"
    python ~/.copilot/tools/learn.py --pattern "Title" "What works well"
    python ~/.copilot/tools/learn.py --decision "Title" "Choice and rationale"

    # Show past mistakes and patterns
    python ~/.copilot/tools/query-session.py --mistakes
    python ~/.copilot/tools/query-session.py --patterns
    python ~/.copilot/tools/query-session.py --decisions
    ```

    ## Workflow

    1. **Before starting**: run `briefing.py` with a description of your task.
    2. **During work**: if you hit an issue, search for similar past problems.
    3. **After finishing**: record any new mistakes, patterns, or decisions
       with `learn.py`.
""")


def deploy_skill():
    """Deploy SKILL.md to the current project directory."""
    project_root = _git_root() or Path.cwd()
    print(f"\nSkill Deployment")
    print(f"  Project: {project_root}")

    # Read source skill content
    if SKILLS_SRC.is_file():
        skill_content = SKILLS_SRC.read_text(encoding="utf-8")
        print(f"  {OK} Source: {_tilde(SKILLS_SRC)}")
    else:
        skill_content = MINIMAL_SKILL_MD
        print(
            f"  {INFO} Source: generating minimal SKILL.md "
            f"(no source template found)"
        )

    deployed = []

    # Copilot CLI: .github/skills/session-knowledge/SKILL.md
    if COPILOT_DIR.is_dir():
        target = (
            project_root / ".github" / "skills"
            / "session-knowledge" / "SKILL.md"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skill_content, encoding="utf-8")
        deployed.append(("Copilot CLI", target))
        print(f"  {OK} Copilot: {target.relative_to(project_root)}")

    # Claude Code: .claude/skills/session-knowledge/SKILL.md
    if CLAUDE_DIR.is_dir():
        target = (
            project_root / ".claude" / "skills"
            / "session-knowledge" / "SKILL.md"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skill_content, encoding="utf-8")
        deployed.append(("Claude Code", target))
        print(f"  {OK} Claude:  {target.relative_to(project_root)}")

    if not deployed:
        print(f"  {FAIL} No agents detected — nothing deployed")
        print(f"      Create ~/.copilot/ or ~/.claude/ first.")
        return

    print(f"\n  Deployed {len(deployed)} skill file(s).")


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
    print(f"    python {inst} --deploy-skill  # Add skill to project")
    print(f"    python {inst} --test          # Run self-test")
    print(f"    python {inst} --uninstall     # Remove tools")


# ===================================================================
# Main
# ===================================================================

def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--deploy-skill" in args:
        deploy_skill()
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
