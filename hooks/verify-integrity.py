#!/usr/bin/env python3
"""verify-integrity.py — sessionStart hook (cross-platform)

Verify hook files haven't been tampered with by comparing SHA256 checksums
against a locked manifest. If tampering detected, warn loudly.
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

HOOKS_DIR = Path.home() / ".copilot" / "tools" / "hooks"
MANIFEST = Path.home() / ".copilot" / "hooks" / "integrity-manifest.json"


def _sha256(filepath):
    """Calculate SHA256 of a file."""
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def main():
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

    if tampered or missing:
        print()
        print("  🚨 HOOK INTEGRITY ALERT 🚨")
        if tampered:
            print(f"  TAMPERED: {', '.join(tampered)}")
        if missing:
            print(f"  MISSING:  {', '.join(missing)}")
        print("  Run: python3 ~/.copilot/tools/install.py --lock-hooks")
        print("  to restore and re-lock hook files.")
        print()
    else:
        count = len(manifest.get("files", {}))
        print(f"  🔒 Hook integrity verified ({count} files)")


if __name__ == "__main__":
    main()
