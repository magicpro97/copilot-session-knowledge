#!/usr/bin/env python3
"""
install.py — Smart installer for session knowledge tools

Usage:
    python install.py                        # Auto-detect and show status
    python install.py --deploy-skill         # Deploy SKILL.md to current project
    python install.py --inject-global        # Add session-knowledge to global copilot-instructions
    python install.py --install-services     # Install auto-start services (systemd/launchd/Task Scheduler)
    python install.py --uninstall-services   # Remove auto-start services
    python install.py --service-status       # Show service status
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

    # Services
    svc = _service_status()
    if svc["watcher"] == "active":
        print(f"  {OK} Services:    installed ({svc['platform']})")
    elif svc["watcher"] is not None:
        print(f"  {INFO} Services:    {svc['watcher']} ({svc['platform']})")
    else:
        print(f"  {FAIL} Services:    not installed (run --install-services)")

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
# 3a. Service Installation (systemd / launchd / Task Scheduler)
# ===================================================================

def _detect_platform() -> str:
    """Detect platform: 'linux', 'macos', or 'windows'."""
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    return "linux"


def _find_python() -> str:
    """Find the best python3 path for service definitions."""
    # Use the currently running interpreter
    exe = sys.executable
    if exe:
        return exe
    # Fallback
    for p in ["/home/linuxbrew/.linuxbrew/bin/python3",
              "/opt/homebrew/bin/python3",
              "/usr/local/bin/python3",
              "/usr/bin/python3"]:
        if Path(p).exists():
            return p
    return "python3"


def _service_status() -> dict:
    """Check status of installed services across platforms."""
    platform = _detect_platform()
    result = {"platform": platform, "watcher": None, "updater": None}

    if platform == "linux":
        for svc, key in [("copilot-watch-sessions.service", "watcher"),
                         ("copilot-auto-update.timer", "updater")]:
            try:
                r = subprocess.run(
                    ["systemctl", "--user", "is-active", svc],
                    capture_output=True, text=True, timeout=5,
                )
                result[key] = r.stdout.strip()  # "active", "inactive", "failed"
            except Exception:
                pass

    elif platform == "macos":
        for label, key in [("com.copilot.watch-sessions", "watcher"),
                           ("com.copilot.auto-update", "updater")]:
            plist = HOME / "Library" / "LaunchAgents" / f"{label}.plist"
            if plist.exists():
                try:
                    r = subprocess.run(
                        ["launchctl", "list", label],
                        capture_output=True, text=True, timeout=5,
                    )
                    result[key] = "active" if r.returncode == 0 else "loaded"
                except Exception:
                    result[key] = "installed"
            else:
                result[key] = None

    elif platform == "windows":
        for task_name, key in [("CopilotWatchSessions", "watcher"),
                               ("CopilotAutoUpdate", "updater")]:
            try:
                r = subprocess.run(
                    ["schtasks", "/Query", "/TN", task_name, "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0 and task_name in r.stdout:
                    result[key] = "active"
            except Exception:
                pass

    return result


def install_services():
    """Install auto-start services for watcher and auto-update."""
    platform = _detect_platform()
    python = _find_python()
    watcher_script = str(TOOLS_DIR / "watch-sessions.py")
    updater_script = str(TOOLS_DIR / "auto-update-tools.py")
    log_dir = SESSION_STATE

    print(f"\nInstalling Services ({platform})")
    print(f"  Python:  {python}")
    print(f"  Watcher: {_tilde(Path(watcher_script))}")
    print(f"  Updater: {_tilde(Path(updater_script))}")

    if platform == "linux":
        _install_systemd(python, watcher_script, updater_script, log_dir)
    elif platform == "macos":
        _install_launchd(python, watcher_script, updater_script, log_dir)
    elif platform == "windows":
        _install_task_scheduler(python, watcher_script, updater_script)
    else:
        print(f"  {FAIL} Unsupported platform: {platform}")
        return

    print(f"\n  {OK} Services installed successfully!")
    print(f"  Run: python {_tilde(TOOLS_DIR / 'install.py')} --service-status")


def _install_systemd(python: str, watcher: str, updater: str, log_dir: Path):
    """Install systemd user services (Linux)."""
    unit_dir = HOME / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)

    # --- watch-sessions.service ---
    watcher_unit = unit_dir / "copilot-watch-sessions.service"
    watcher_content = textwrap.dedent(f"""\
        [Unit]
        Description=Copilot Session Knowledge Watcher
        After=default.target
        StartLimitIntervalSec=300
        StartLimitBurst=5

        [Service]
        Type=simple
        ExecStart={python} {watcher} --interval 60
        WorkingDirectory={TOOLS_DIR}
        Restart=always
        RestartSec=10
        StandardOutput=append:{log_dir}/watch-sessions.log
        StandardError=append:{log_dir}/watch-sessions.error.log
        Environment="PATH={Path(python).parent}:/usr/local/bin:/usr/bin:/bin"
        Environment="PYTHONUNBUFFERED=1"

        [Install]
        WantedBy=default.target
    """)
    watcher_unit.write_text(watcher_content, encoding="utf-8")
    print(f"  {OK} Created {_tilde(watcher_unit)}")

    # --- auto-update.service (oneshot) ---
    updater_unit = unit_dir / "copilot-auto-update.service"
    updater_content = textwrap.dedent(f"""\
        [Unit]
        Description=Copilot Session Knowledge Auto-Update
        After=network.target

        [Service]
        Type=oneshot
        ExecStart={python} {updater} --force
        WorkingDirectory={TOOLS_DIR}
        StandardOutput=append:{log_dir}/auto-update.log
        StandardError=append:{log_dir}/auto-update.error.log
        Environment="PATH={Path(python).parent}:/usr/local/bin:/usr/bin:/bin"
        Environment="HOME={HOME}"
    """)
    updater_unit.write_text(updater_content, encoding="utf-8")
    print(f"  {OK} Created {_tilde(updater_unit)}")

    # --- auto-update.timer ---
    timer_unit = unit_dir / "copilot-auto-update.timer"
    timer_content = textwrap.dedent("""\
        [Unit]
        Description=Copilot Session Knowledge Auto-Update Timer

        [Timer]
        OnBootSec=5min
        OnUnitActiveSec=24h
        Persistent=true

        [Install]
        WantedBy=timers.target
    """)
    timer_unit.write_text(timer_content, encoding="utf-8")
    print(f"  {OK} Created {_tilde(timer_unit)}")

    # Reload, enable, start
    cmds = [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "copilot-watch-sessions.service"],
        ["systemctl", "--user", "enable", "--now", "copilot-auto-update.timer"],
    ]
    for cmd in cmds:
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception as e:
            print(f"  {FAIL} {' '.join(cmd)}: {e}")
            return
    print(f"  {OK} Services enabled and started")

    # Enable linger (survive logout)
    try:
        subprocess.run(
            ["loginctl", "enable-linger", os.environ.get("USER", "")],
            capture_output=True, text=True, timeout=10,
        )
        print(f"  {OK} Linger enabled (services persist after logout)")
    except Exception:
        print(f"  {INFO} Could not enable linger — services may stop on logout")


def _install_launchd(python: str, watcher: str, updater: str, log_dir: Path):
    """Install launchd agents (macOS)."""
    agents_dir = HOME / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    # --- Watch Sessions ---
    watcher_plist = agents_dir / "com.copilot.watch-sessions.plist"
    watcher_content = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>com.copilot.watch-sessions</string>

            <key>ProgramArguments</key>
            <array>
                <string>{python}</string>
                <string>{watcher}</string>
                <string>--interval</string>
                <string>60</string>
            </array>

            <key>WorkingDirectory</key>
            <string>{TOOLS_DIR}</string>

            <key>RunAtLoad</key>
            <true/>

            <key>KeepAlive</key>
            <dict>
                <key>SuccessfulExit</key>
                <false/>
            </dict>

            <key>StandardOutPath</key>
            <string>{log_dir}/watch-sessions.log</string>

            <key>StandardErrorPath</key>
            <string>{log_dir}/watch-sessions.error.log</string>

            <key>ThrottleInterval</key>
            <integer>30</integer>

            <key>EnvironmentVariables</key>
            <dict>
                <key>PYTHONUNBUFFERED</key>
                <string>1</string>
            </dict>
        </dict>
        </plist>
    """)
    watcher_plist.write_text(watcher_content, encoding="utf-8")
    print(f"  {OK} Created {_tilde(watcher_plist)}")

    # --- Auto-Update (runs every 24h) ---
    updater_plist = agents_dir / "com.copilot.auto-update.plist"
    updater_content = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>com.copilot.auto-update</string>

            <key>ProgramArguments</key>
            <array>
                <string>{python}</string>
                <string>{updater}</string>
                <string>--force</string>
            </array>

            <key>WorkingDirectory</key>
            <string>{TOOLS_DIR}</string>

            <key>StartInterval</key>
            <integer>86400</integer>

            <key>RunAtLoad</key>
            <true/>

            <key>StandardOutPath</key>
            <string>{log_dir}/auto-update.log</string>

            <key>StandardErrorPath</key>
            <string>{log_dir}/auto-update.error.log</string>

            <key>EnvironmentVariables</key>
            <dict>
                <key>PATH</key>
                <string>{Path(python).parent}:/usr/local/bin:/usr/bin:/bin</string>
            </dict>
        </dict>
        </plist>
    """)
    updater_plist.write_text(updater_content, encoding="utf-8")
    print(f"  {OK} Created {_tilde(updater_plist)}")

    # Load agents
    for label, plist in [("com.copilot.watch-sessions", watcher_plist),
                         ("com.copilot.auto-update", updater_plist)]:
        try:
            # Unload first if already loaded
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                print(f"  {OK} Loaded {label}")
            else:
                # Fallback for older macOS
                subprocess.run(
                    ["launchctl", "load", "-w", str(plist)],
                    capture_output=True, text=True, timeout=10,
                )
                print(f"  {OK} Loaded {label} (legacy)")
        except Exception as e:
            print(f"  {FAIL} Could not load {label}: {e}")


def _install_task_scheduler(python: str, watcher: str, updater: str):
    """Install Windows Task Scheduler tasks."""
    # --- Watch Sessions (runs at logon, restarts on failure) ---
    # Create a wrapper .bat that restarts python if it crashes
    bat_dir = TOOLS_DIR / "scripts"
    bat_dir.mkdir(parents=True, exist_ok=True)

    watcher_bat = bat_dir / "watch-sessions.bat"
    watcher_bat.write_text(
        f'@echo off\r\n'
        f'"{python}" "{watcher}" --interval 60\r\n',
        encoding="utf-8",
    )

    updater_bat = bat_dir / "auto-update.bat"
    updater_bat.write_text(
        f'@echo off\r\n'
        f'cd /d "{TOOLS_DIR}"\r\n'
        f'"{python}" "{updater}" --force\r\n',
        encoding="utf-8",
    )

    # Use PowerShell to create scheduled tasks (more reliable than schtasks for complex configs)
    ps_script = textwrap.dedent(f"""\
        # Watch Sessions — runs at logon, restarts on failure
        $watchAction = New-ScheduledTaskAction `
            -Execute '"{python}"' `
            -Argument '"{watcher}" --interval 60' `
            -WorkingDirectory '"{TOOLS_DIR}"'

        $watchTrigger = New-ScheduledTaskTrigger -AtLogOn
        $watchSettings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -RestartCount 999 `
            -ExecutionTimeLimit (New-TimeSpan -Days 365)

        Unregister-ScheduledTask -TaskName 'CopilotWatchSessions' -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask `
            -TaskName 'CopilotWatchSessions' `
            -Description 'Copilot Session Knowledge Watcher' `
            -Action $watchAction `
            -Trigger $watchTrigger `
            -Settings $watchSettings `
            -RunLevel Limited

        # Auto-Update — runs daily
        $updateAction = New-ScheduledTaskAction `
            -Execute '"{python}"' `
            -Argument '-c "import subprocess,sys; subprocess.run([\\\"bash\\\", \\\"{updater}\\\", \\\"--force\\\"])"' `
            -WorkingDirectory '"{TOOLS_DIR}"'

        $updateTrigger = New-ScheduledTaskTrigger -Daily -At '03:00AM'
        $updateSettings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable

        Unregister-ScheduledTask -TaskName 'CopilotAutoUpdate' -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask `
            -TaskName 'CopilotAutoUpdate' `
            -Description 'Copilot Session Knowledge Auto-Update (daily)' `
            -Action $updateAction `
            -Trigger $updateTrigger `
            -Settings $updateSettings `
            -RunLevel Limited

        # Start watcher now
        Start-ScheduledTask -TaskName 'CopilotWatchSessions'
    """)

    ps_file = bat_dir / "install-tasks.ps1"
    ps_file.write_text(ps_script, encoding="utf-8")

    try:
        r = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps_file)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            print(f"  {OK} Created Task: CopilotWatchSessions (runs at logon)")
            print(f"  {OK} Created Task: CopilotAutoUpdate (daily at 3:00 AM)")
            print(f"  {OK} Watcher started")
        else:
            # Fallback to schtasks.exe
            print(f"  {INFO} PowerShell failed, trying schtasks...")
            _install_schtasks_fallback(python, watcher, updater)
    except FileNotFoundError:
        # No PowerShell, use schtasks
        _install_schtasks_fallback(python, watcher, updater)
    except Exception as e:
        print(f"  {FAIL} Task Scheduler error: {e}")


def _install_schtasks_fallback(python: str, watcher: str, updater: str):
    """Fallback: use schtasks.exe directly (less features but more compatible)."""
    try:
        # Watcher: run at logon
        subprocess.run([
            "schtasks", "/Create", "/F",
            "/TN", "CopilotWatchSessions",
            "/TR", f'"{python}" "{watcher}" --interval 60',
            "/SC", "ONLOGON",
            "/RL", "LIMITED",
        ], capture_output=True, text=True, timeout=15, check=True)
        print(f"  {OK} Created CopilotWatchSessions (schtasks)")

        # Updater: daily at 3 AM
        subprocess.run([
            "schtasks", "/Create", "/F",
            "/TN", "CopilotAutoUpdate",
            "/TR", f'"{python}" -c "import subprocess; subprocess.run([\'bash\', \'{updater}\', \'--force\'])"',
            "/SC", "DAILY",
            "/ST", "03:00",
            "/RL", "LIMITED",
        ], capture_output=True, text=True, timeout=15, check=True)
        print(f"  {OK} Created CopilotAutoUpdate (schtasks)")

        # Start watcher now
        subprocess.run(
            ["schtasks", "/Run", "/TN", "CopilotWatchSessions"],
            capture_output=True, text=True, timeout=10,
        )
        print(f"  {OK} Watcher started")
    except Exception as e:
        print(f"  {FAIL} schtasks error: {e}")


def uninstall_services():
    """Remove auto-start services from the current platform."""
    platform = _detect_platform()
    print(f"\nUninstalling Services ({platform})")

    if platform == "linux":
        cmds = [
            ["systemctl", "--user", "disable", "--now", "copilot-watch-sessions.service"],
            ["systemctl", "--user", "disable", "--now", "copilot-auto-update.timer"],
            ["systemctl", "--user", "stop", "copilot-auto-update.service"],
        ]
        for cmd in cmds:
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            except Exception:
                pass

        unit_dir = HOME / ".config" / "systemd" / "user"
        for name in ["copilot-watch-sessions.service",
                      "copilot-auto-update.service",
                      "copilot-auto-update.timer"]:
            f = unit_dir / name
            if f.exists():
                f.unlink()
                print(f"  {OK} Removed {_tilde(f)}")

        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            pass

    elif platform == "macos":
        agents_dir = HOME / "Library" / "LaunchAgents"
        for label in ["com.copilot.watch-sessions", "com.copilot.auto-update"]:
            plist = agents_dir / f"{label}.plist"
            if plist.exists():
                try:
                    subprocess.run(
                        ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)],
                        capture_output=True, text=True, timeout=10,
                    )
                except Exception:
                    try:
                        subprocess.run(
                            ["launchctl", "unload", str(plist)],
                            capture_output=True, text=True, timeout=10,
                        )
                    except Exception:
                        pass
                plist.unlink()
                print(f"  {OK} Removed {_tilde(plist)}")

    elif platform == "windows":
        for task_name in ["CopilotWatchSessions", "CopilotAutoUpdate"]:
            try:
                r = subprocess.run(
                    ["schtasks", "/Delete", "/TN", task_name, "/F"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0:
                    print(f"  {OK} Removed {task_name}")
            except Exception:
                pass

        # Clean up bat/ps1 scripts
        scripts_dir = TOOLS_DIR / "scripts"
        if scripts_dir.is_dir():
            shutil.rmtree(str(scripts_dir), ignore_errors=True)
            print(f"  {OK} Removed {_tilde(scripts_dir)}")

    print(f"  {OK} Services uninstalled")


def show_service_status():
    """Show status of installed services."""
    status = _service_status()
    platform = status["platform"]
    print(f"\nService Status ({platform})")

    labels = {"watcher": "Watch Sessions", "updater": "Auto-Update"}
    for key in ["watcher", "updater"]:
        state = status[key]
        label = labels[key]
        if state == "active":
            print(f"  {OK} {label}: running")
        elif state is None:
            print(f"  {FAIL} {label}: not installed")
        else:
            print(f"  {INFO} {label}: {state}")

    # Platform-specific details
    if platform == "linux":
        print(f"\n  Commands:")
        print(f"    systemctl --user status copilot-watch-sessions")
        print(f"    systemctl --user restart copilot-watch-sessions")
        print(f"    journalctl --user -u copilot-watch-sessions -f")
        print(f"    systemctl --user list-timers")
    elif platform == "macos":
        print(f"\n  Commands:")
        print(f"    launchctl list | grep copilot")
        print(f"    tail -f ~/.copilot/session-state/watch-sessions.log")
    elif platform == "windows":
        print(f"\n  Commands:")
        print(f"    schtasks /Query /TN CopilotWatchSessions")
        print(f"    schtasks /Query /TN CopilotAutoUpdate")
        print(f"    Get-ScheduledTask -TaskName Copilot*  (PowerShell)")


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
    inst = _tilde(TOOLS_DIR / "install.py")
    print(f"\n  Quick start:")
    print(f"    python {qs} \"search terms\"   # Search knowledge base")
    print(f"    python {br} \"your task\"       # Context briefing")
    print(f"\n  Management:")
    print(f"    python {inst} --install-services    # Auto-start watcher + updater")
    print(f"    python {inst} --service-status      # Check service status")
    print(f"    python {inst} --deploy-skill        # Add skill to project")
    print(f"    python {inst} --inject-global       # Add to global copilot-instructions")
    print(f"    python {inst} --test                # Run self-test")
    print(f"    python {inst} --uninstall           # Remove tools")


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

    if "--inject-global" in args:
        inject_global()
        return

    if "--install-services" in args:
        install_services()
        return

    if "--uninstall-services" in args:
        uninstall_services()
        return

    if "--service-status" in args:
        show_service_status()
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
