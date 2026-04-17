#!/usr/bin/env python3
"""verify-integrity.py — sessionStart hook (cross-platform)

Verify hook files haven't been tampered with. If tampered, create a
kill-switch marker that blocks ALL preToolUse operations until re-locked.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from marker_auth import create_tamper_marker
except ImportError:
    def create_tamper_marker():
        p = Path.home() / ".copilot" / "markers" / "hooks-tampered"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()

HOOKS_DIR = Path.home() / ".copilot" / "tools" / "hooks"
HOOKS_DST_DIR = Path.home() / ".copilot" / "hooks"
MANIFEST = HOOKS_DST_DIR / "integrity-manifest.json"
CONFIG = Path.home() / ".copilot" / "config.json"


def _sha256(filepath):
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _check_config_poisoning():
    """Check if config.json has been poisoned with disableAllHooks."""
    try:
        if CONFIG.is_file():
            cfg = json.loads(CONFIG.read_text())
            if cfg.get("disableAllHooks"):
                return True
    except Exception:
        pass
    return False


def main():
    # Check config.json poisoning FIRST (even without manifest)
    if _check_config_poisoning():
        print()
        print("  🚨 CONFIG POISONED: disableAllHooks detected in config.json!")
        print("  This disables ALL hook enforcement.")
        print("  Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks")
        print()
        create_tamper_marker()
        return

    if not MANIFEST.is_file():
        return

    try:
        manifest = json.loads(MANIFEST.read_text())
    except Exception:
        return

    tampered = []
    missing = []

    for filename, expected_hash in manifest.get("files", {}).items():
        filepath = HOOKS_DIR / filename
        if not filepath.is_file():
            missing.append(filename)
            continue
        actual_hash = _sha256(filepath)
        if actual_hash != expected_hash:
            tampered.append(filename)

    hooks_json_hash = manifest.get("hooks_json")
    if hooks_json_hash:
        hooks_json_path = HOOKS_DST_DIR / "hooks.json"
        if hooks_json_path.is_file():
            actual = hashlib.sha256(hooks_json_path.read_bytes()).hexdigest()
            if actual != hooks_json_hash:
                tampered.append("hooks.json")
        else:
            tampered.append("hooks.json (MISSING)")

    if tampered or missing:
        print()
        print("  🚨 HOOK INTEGRITY ALERT 🚨")
        if tampered:
            print(f"  TAMPERED: {', '.join(tampered)}")
        if missing:
            print(f"  MISSING:  {', '.join(missing)}")
        print("  Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks")
        print()
        # Activate kill-switch
        create_tamper_marker()
    else:
        count = len(manifest.get("files", {}))
        print(f"  🔒 Hook integrity verified ({count} files + hooks.json)")
        # Clear tamper marker if integrity restored
        tamper_path = Path.home() / ".copilot" / "markers" / "hooks-tampered"
        if tamper_path.is_file():
            try:
                tamper_path.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    main()
