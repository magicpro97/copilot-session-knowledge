#!/usr/bin/env python3
"""
install.py — Set up Copilot Session Knowledge tools

One-command setup for Windows, macOS, and Linux.
Creates ~/.copilot/tools/, installs all scripts, and builds initial index.

Usage:
    python install.py              # Install and build index
    python install.py --check      # Check installation status
    python install.py --uninstall  # Remove tools (keeps knowledge.db)
"""

import os
import shutil
import sys

# Fix Windows console encoding for Unicode output
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from pathlib import Path

TOOLS_DIR = Path.home() / ".copilot" / "tools"
SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"

TOOL_FILES = [
    "build-session-index.py",
    "query-session.py",
    "watch-sessions.py",
    "extract-knowledge.py",
    "embed.py",
    "briefing.py",
    "learn.py",
    "claude-adapter.py",
    "sync-knowledge.py",
    "install.py",
]


def check_status():
    """Check installation status."""
    print(f"\nCopilot Session Knowledge Tools — Status")
    print(f"{'='*50}")

    # Tools directory
    print(f"\nTools directory: {TOOLS_DIR}")
    if TOOLS_DIR.exists():
        print(f"  ✓ Exists")
        for f in TOOL_FILES:
            path = TOOLS_DIR / f
            status = "✓" if path.exists() else "✗ MISSING"
            size = f"({path.stat().st_size // 1024}KB)" if path.exists() else ""
            print(f"  {status} {f} {size}")
    else:
        print(f"  ✗ Not found")

    # Session state
    print(f"\nSession state: {SESSION_STATE}")
    if SESSION_STATE.exists():
        sessions = [d for d in SESSION_STATE.iterdir() if d.is_dir() and not d.name.startswith(".")]
        print(f"  ✓ {len(sessions)} sessions found")
    else:
        print(f"  ✗ Not found")

    # Knowledge DB
    print(f"\nKnowledge database: {DB_PATH}")
    if DB_PATH.exists():
        import sqlite3
        db = sqlite3.connect(str(DB_PATH))
        docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        try:
            entries = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
        except sqlite3.OperationalError:
            entries = 0
        db.close()
        print(f"  ✓ {docs} documents indexed, {entries} knowledge entries")
        print(f"  Size: {DB_PATH.stat().st_size // 1024}KB")
    else:
        print(f"  ✗ Not built (run install to build)")

    # Python version
    print(f"\nPython: {sys.version.split()[0]}")
    print(f"Platform: {sys.platform}")


def install():
    """Install tools and build index."""
    print(f"Installing Copilot Session Knowledge Tools...")
    print(f"Target: {TOOLS_DIR}")
    print()

    # 1. Create tools directory
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  ✓ Tools directory ready")

    # 2. Copy scripts (if running from a different location)
    source_dir = Path(__file__).parent
    if source_dir.resolve() != TOOLS_DIR.resolve():
        for f in TOOL_FILES:
            src = source_dir / f
            dst = TOOLS_DIR / f
            if src.exists():
                shutil.copy2(str(src), str(dst))
                print(f"  ✓ Installed {f}")
            else:
                print(f"  ⚠ {f} not found in source directory")
    else:
        print(f"  ✓ Scripts already in place")

    # 3. Build initial index
    print(f"\nBuilding knowledge index...")
    if SESSION_STATE.exists():
        import subprocess
        indexer = TOOLS_DIR / "build-session-index.py"
        result = subprocess.run(
            [sys.executable, str(indexer)],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            # Show summary lines
            for line in result.stdout.splitlines():
                if any(k in line.lower() for k in ["indexed", "sessions:", "documents:", "fts"]):
                    print(f"  {line.strip()}")
        else:
            print(f"  ⚠ Indexer error: {result.stderr[:200]}")

        # 4. Run knowledge extraction
        extractor = TOOLS_DIR / "extract-knowledge.py"
        if extractor.exists():
            print(f"\nExtracting knowledge...")
            result = subprocess.run(
                [sys.executable, str(extractor)],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if any(k in line.lower() for k in ["extracted", "total", "category"]):
                        print(f"  {line.strip()}")
    else:
        print(f"  ⚠ No session-state directory found. Index will be built on first use.")

    # 5. Check optional dependencies
    print(f"\nOptional dependencies:")
    try:
        import sklearn  # noqa: F401
        print(f"  ✓ scikit-learn installed (TF-IDF fallback)")
    except ImportError:
        print(f"  ℹ scikit-learn not installed (optional TF-IDF fallback)")
        print(f"    Install with: pip install scikit-learn")

    # 6. Summary
    print(f"\n{'='*50}")
    print(f"  Installation complete!")
    print(f"{'='*50}")
    print(f"\nUsage:")
    qs_path = TOOLS_DIR / 'query-session.py'
    ws_path = TOOLS_DIR / 'watch-sessions.py'
    em_path = TOOLS_DIR / 'embed.py'
    print(f"  python {qs_path} \"search terms\"")
    print(f"  python {qs_path} \"search terms\" --semantic")
    print(f"  python {qs_path} --list")
    print(f"  python {qs_path} --mistakes")
    print(f"  python {ws_path}  # Auto-index daemon")
    print(f"  python {em_path} --setup    # Configure embedding provider")
    print(f"  python {em_path} --build    # Generate embeddings")
    print(f"\nShort aliases (add to shell profile):")
    print(f"  alias qs='python {qs_path}'")
    print(f"  alias qss='python {qs_path} --semantic'")
    print(f"  alias qsm='python {qs_path} --mistakes'")


def uninstall():
    """Remove tools (keeps knowledge.db for data preservation)."""
    print(f"Uninstalling Copilot Session Knowledge Tools...")

    for f in TOOL_FILES:
        path = TOOLS_DIR / f
        if path.exists():
            path.unlink()
            print(f"  ✓ Removed {f}")

    # Remove watch state file
    state_file = SESSION_STATE / ".watch-state.json"
    if state_file.exists():
        state_file.unlink()
        print(f"  ✓ Removed watch state")

    # Keep knowledge.db
    if DB_PATH.exists():
        print(f"  ℹ Kept {DB_PATH} (delete manually if not needed)")

    # Remove tools dir if empty
    if TOOLS_DIR.exists() and not any(TOOLS_DIR.iterdir()):
        TOOLS_DIR.rmdir()
        print(f"  ✓ Removed empty tools directory")

    print(f"\nUninstall complete.")


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--check" in args:
        check_status()
        return

    if "--uninstall" in args:
        uninstall()
        return

    install()


if __name__ == "__main__":
    main()
