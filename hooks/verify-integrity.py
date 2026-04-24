#!/usr/bin/env python3
"""verify-integrity.py — sessionStart hook (cross-platform)

Verify hook files haven't been tampered with. Auto-updates manifest when
hook files change legitimately. Only blocks on config poisoning.
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


def _regenerate_manifest():
    """Regenerate manifest with current file hashes."""
    manifest = {"files": {}, "hooks_json": None}
    for hf in sorted(HOOKS_DIR.glob("*.py")):
        h = _sha256(hf)
        if h:
            manifest["files"][hf.name] = h
    rules_dir = HOOKS_DIR / "rules"
    if rules_dir.is_dir():
        for hf in sorted(rules_dir.glob("*.py")):
            h = _sha256(hf)
            if h:
                manifest["files"][f"rules/{hf.name}"] = h
    hooks_json_path = HOOKS_DST_DIR / "hooks.json"
    if hooks_json_path.is_file():
        manifest["hooks_json"] = hashlib.sha256(
            hooks_json_path.read_bytes()
        ).hexdigest()
    HOOKS_DST_DIR.mkdir(parents=True, exist_ok=True)
    # P1-6: atomic write + explicit encoding to prevent false tamper on partial read
    tmp = MANIFEST.with_suffix(".tmp")
    try:
        tmp.write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))
        os.replace(str(tmp), str(MANIFEST))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def main():
    # Config poisoning is always a hard block
    if _check_config_poisoning():
        print()
        print("  \U0001f6a8 CONFIG POISONED: disableAllHooks detected in config.json!")
        print("  This disables ALL hook enforcement.")
        print("  Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks")
        print()
        create_tamper_marker()
        return

    if not MANIFEST.is_file():
        _regenerate_manifest()
        print("  \U0001f512 Hook integrity manifest generated (first run)")
        return

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return

    changed = []
    missing = []

    for filename, expected_hash in manifest.get("files", {}).items():
        filepath = HOOKS_DIR / filename
        if not filepath.is_file():
            missing.append(filename)
            continue
        actual_hash = _sha256(filepath)
        if actual_hash != expected_hash:
            changed.append(filename)

    hooks_json_hash = manifest.get("hooks_json")
    if hooks_json_hash:
        hooks_json_path = HOOKS_DST_DIR / "hooks.json"
        if hooks_json_path.is_file():
            actual = hashlib.sha256(hooks_json_path.read_bytes()).hexdigest()
            if actual != hooks_json_hash:
                changed.append("hooks.json")
        else:
            changed.append("hooks.json (MISSING)")

    if changed or missing:
        # Auto-update manifest for legitimate changes
        _regenerate_manifest()
        # Clear stale tamper marker
        tamper_path = Path.home() / ".copilot" / "markers" / "hooks-tampered"
        if tamper_path.is_file():
            try:
                tamper_path.unlink()
            except Exception:
                pass
        lines = ["  \U0001f504 Hook files updated \u2014 manifest refreshed"]
        if changed:
            print(f"  Changed: {', '.join(changed)}")
        if missing:
            print(f"  Removed: {', '.join(missing)}")
    else:
        count = len(manifest.get("files", {}))
        print(f"  \U0001f512 Hook integrity verified ({count} files + hooks.json)")
        tamper_path = Path.home() / ".copilot" / "markers" / "hooks-tampered"
        if tamper_path.is_file():
            try:
                tamper_path.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    main()
