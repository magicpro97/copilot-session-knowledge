#!/usr/bin/env python3
"""
install.py — Smart installer for session knowledge tools

Usage:
    python install.py                        # Auto-detect and show status
    python install.py --deploy-skill         # Deploy SKILL.md to current project
    python install.py --deploy-hooks         # Deploy hooks.json to ~/.copilot/hooks/
    python install.py --lock-hooks           # Lock hooks with OS immutable flags
    python install.py --unlock-hooks         # Unlock hooks for updates
    python install.py --deploy-instructions  # Deploy global instructions to ~/.github/
    python install.py --inject-global        # Add session-knowledge to global copilot-instructions
    python install.py --test                 # Run self-test
    python install.py --uninstall            # Remove installed files
    python install.py --help                 # Show this help
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
GLOBAL_INSTRUCTIONS = HOME / ".github" / "copilot-instructions.md"
LOCK_FILE = SESSION_STATE / ".watcher.lock"
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

    # Compact context for sub-agent prompts
    python3 ~/.copilot/tools/briefing.py "task description" --for-subagent

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
    4. **Sub-agents**: run `briefing.py --for-subagent` and inject output into
       sub-agent prompts.
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
# 2b. Global Instructions Injection
# ===================================================================

_INJECT_MARKER_START = "<!-- SESSION-KNOWLEDGE-START -->"
_INJECT_MARKER_END = "<!-- SESSION-KNOWLEDGE-END -->"

GLOBAL_INJECT_BLOCK = textwrap.dedent(f"""\
{_INJECT_MARKER_START}
## 🧠 Session Knowledge — BẮT BUỘC

**TRƯỚC KHI bắt đầu bất kỳ task phức tạp nào**, AI agent PHẢI chạy briefing:

```bash
python3 ~/.copilot/tools/briefing.py "mô tả task" --full
```

**Khi dispatch sub-agents**, inject context vào prompt:
```bash
python3 ~/.copilot/tools/briefing.py "task cho sub-agent" --for-subagent
```

**Khi gặp lỗi**, search knowledge base trước khi debug từ đầu:
```bash
python3 ~/.copilot/tools/query-session.py "error message" --verbose
```

**Sau khi giải quyết xong vấn đề phức tạp**, ghi nhận kinh nghiệm:
```bash
python3 ~/.copilot/tools/learn.py --mistake "Tiêu đề" "Mô tả lỗi và cách fix"
python3 ~/.copilot/tools/learn.py --pattern "Tiêu đề" "Pattern hiệu quả"
python3 ~/.copilot/tools/learn.py --decision "Tiêu đề" "Quyết định và lý do"
```

### Quy tắc
- ✅ **Luôn chạy** `briefing.py` trước task phức tạp (debug, refactor, migration, E2E test)
- ✅ **Luôn search** knowledge khi gặp lỗi lạ hoặc cần quyết định kiến trúc
- ✅ **Luôn inject** `--for-subagent` context khi dispatch explore/task/general-purpose agents
- ✅ **Luôn ghi nhận** mistakes/patterns/decisions sau khi giải quyết vấn đề mới
- ❌ **KHÔNG ĐƯỢC** bỏ qua briefing rồi lặp lại sai lầm đã ghi nhận
- ❌ **KHÔNG ĐƯỢC** debug từ đầu khi knowledge DB đã có solution
{_INJECT_MARKER_END}
""")


_TEMPLATES_DIR = _SCRIPT_DIR / "templates"
_INSTRUCTIONS_TEMPLATES = _TEMPLATES_DIR / "instructions"


def deploy_hooks():
    """Deploy hooks.json and Python hook scripts to ~/.copilot/hooks/."""
    print("\nDeploy Hooks")

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
            hooks_dst.write_text(new, encoding="utf-8")
            print(f"  {OK} hooks.json — updated (backup: {backup.name})")
    else:
        hooks_dst.write_text(new, encoding="utf-8")
        print(f"  {OK} hooks.json — created")

    # Ensure markers directory exists
    markers_dir = COPILOT_DIR / "markers"
    markers_dir.mkdir(parents=True, exist_ok=True)
    print(f"  {OK} markers/ directory ready")

    # List available hooks
    hooks_dir = _SCRIPT_DIR / "hooks"
    py_hooks = sorted(hooks_dir.glob("*.py")) if hooks_dir.is_dir() else []
    print(f"\n  {len(py_hooks)} Python hook scripts available:")
    for h in py_hooks:
        print(f"    • {h.name}")




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

    print(f"  {INFO} AI agents will now be required to run briefing.py before complex tasks")


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
    print(f"    python {inst} --deploy-instructions   # Deploy global instructions")
    print(f"    python {inst} --inject-global         # Add to global copilot-instructions")
    print(f"    python {inst} --test                  # Run self-test")
    print(f"    python {inst} --uninstall             # Remove tools")


# ===================================================================
# Main
# ===================================================================


def lock_hooks():
    """Lock hook files with OS-level immutable flags + SHA256 manifest.
    
    macOS: chflags uchg (user immutable — no sudo needed to set, needs nouchg to clear)
    Linux: chattr +i (needs sudo/root)
    Windows: attrib +R
    """
    import hashlib
    import platform

    hooks_dir = _SCRIPT_DIR / "hooks"
    hooks_dst_dir = COPILOT_DIR / "hooks"
    hooks_dst = hooks_dst_dir / "hooks.json"

    print("\n🔒 Lock Hooks — Tamper Protection")

    # Collect all hook files to protect
    hook_files = sorted(hooks_dir.glob("*.py")) if hooks_dir.is_dir() else []
    if not hook_files:
        print(f"  {FAIL} No hook scripts found in {_tilde(hooks_dir)}")
        return

    # 1. Generate SHA256 manifest
    manifest = {"files": {}, "hooks_json": None}
    for hf in hook_files:
        h = hashlib.sha256(hf.read_bytes()).hexdigest()
        manifest["files"][hf.name] = h
        print(f"  {OK} {hf.name}: {h[:16]}...")

    if hooks_dst.is_file():
        h = hashlib.sha256(hooks_dst.read_bytes()).hexdigest()
        manifest["hooks_json"] = h
        print(f"  {OK} hooks.json: {h[:16]}...")

    # Save manifest
    manifest_path = hooks_dst_dir / "integrity-manifest.json"
    hooks_dst_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\n  {OK} Manifest saved: {_tilde(manifest_path)}")

    # 2. Set OS-level immutable flags
    system = platform.system()
    protected = 0

    files_to_lock = list(hook_files) + [hooks_dst, manifest_path]

    if system == "Darwin":
        for f in files_to_lock:
            if f.is_file():
                result = subprocess.run(["chflags", "uchg", str(f)],
                                       capture_output=True, text=True)
                if result.returncode == 0:
                    protected += 1
                else:
                    print(f"  {FAIL} chflags failed: {f.name}: {result.stderr.strip()}")
        print(f"\n  {OK} {protected} files locked (chflags uchg)")
        print("  To unlock: python3 install.py --unlock-hooks")

    elif system == "Linux":
        # chattr +i requires root
        for f in files_to_lock:
            if f.is_file():
                result = subprocess.run(["sudo", "chattr", "+i", str(f)],
                                       capture_output=True, text=True)
                if result.returncode == 0:
                    protected += 1
                else:
                    # Try without sudo
                    result2 = subprocess.run(["chattr", "+i", str(f)],
                                            capture_output=True, text=True)
                    if result2.returncode == 0:
                        protected += 1
                    else:
                        print(f"  {FAIL} chattr failed: {f.name} (needs root)")
        if protected:
            print(f"\n  {OK} {protected} files locked (chattr +i)")
        else:
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
    """Remove OS-level immutable flags from hook files."""
    import platform

    hooks_dir = _SCRIPT_DIR / "hooks"
    hooks_dst_dir = COPILOT_DIR / "hooks"
    hooks_dst = hooks_dst_dir / "hooks.json"
    manifest_path = hooks_dst_dir / "integrity-manifest.json"

    print("\n🔓 Unlock Hooks")

    hook_files = sorted(hooks_dir.glob("*.py")) if hooks_dir.is_dir() else []
    files_to_unlock = list(hook_files) + [hooks_dst, manifest_path]

    system = platform.system()
    unlocked = 0

    if system == "Darwin":
        for f in files_to_unlock:
            if f.is_file():
                result = subprocess.run(["chflags", "nouchg", str(f)],
                                       capture_output=True, text=True)
                if result.returncode == 0:
                    unlocked += 1
        print(f"  {OK} {unlocked} files unlocked (chflags nouchg)")

    elif system == "Linux":
        for f in files_to_unlock:
            if f.is_file():
                result = subprocess.run(["sudo", "chattr", "-i", str(f)],
                                       capture_output=True, text=True)
                if result.returncode == 0:
                    unlocked += 1
                else:
                    result2 = subprocess.run(["chattr", "-i", str(f)],
                                            capture_output=True, text=True)
                    if result2.returncode == 0:
                        unlocked += 1
        print(f"  {OK} {unlocked} files unlocked (chattr -i)")

    elif system == "Windows":
        for f in files_to_unlock:
            if f.is_file():
                result = subprocess.run(["attrib", "-R", str(f)],
                                       capture_output=True, text=True)
                if result.returncode == 0:
                    unlocked += 1
        print(f"  {OK} {unlocked} files unlocked (attrib -R)")

    print("  ⚠️  Re-lock after updates: python3 install.py --lock-hooks")


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--deploy-skill" in args:
        deploy_skill()
        return

    if "--deploy-hooks" in args:
        deploy_hooks()
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
