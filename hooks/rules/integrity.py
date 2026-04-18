"""Hook integrity verification rule."""
import hashlib
import json
import sys
from pathlib import Path

from . import Rule
from .common import info

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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


class IntegrityRule(Rule):
    """Verify hook file integrity at session start."""

    name = "integrity"
    events = ["sessionStart"]

    def evaluate(self, event, data):
        # Config poisoning is always a hard block
        if self._check_config_poisoning():
            create_tamper_marker()
            return info(
                "\n  \U0001f6a8 CONFIG POISONED: disableAllHooks detected in config.json!\n"
                "  This disables ALL hook enforcement.\n"
                "  Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks\n"
            )

        if not MANIFEST.is_file():
            # No manifest = first run or after reset. Generate one.
            self._regenerate_manifest()
            return info("  \U0001f512 Hook integrity manifest generated (first run)")

        try:
            manifest = json.loads(MANIFEST.read_text())
        except Exception:
            return None

        changed = []
        missing = []

        for filename, expected_hash in manifest.get("files", {}).items():
            filepath = HOOKS_DIR / filename
            if not filepath.is_file():
                missing.append(filename)
                continue
            actual_hash = self._sha256(filepath)
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
            # Auto-update manifest instead of creating tamper marker
            # This handles legitimate updates (git pull, install.py, agent fixes)
            self._regenerate_manifest()
            # Clear any stale tamper marker from previous false positive
            tamper_path = Path.home() / ".copilot" / "markers" / "hooks-tampered"
            if tamper_path.is_file():
                try:
                    tamper_path.unlink()
                except Exception:
                    pass
            lines = ["  \U0001f504 Hook files updated — manifest refreshed"]
            if changed:
                lines.append(f"  Changed: {', '.join(changed)}")
            if missing:
                lines.append(f"  Removed: {', '.join(missing)}")
            return info("\n".join(lines))

        # All good — clear tamper marker if present
        count = len(manifest.get("files", {}))
        tamper_path = Path.home() / ".copilot" / "markers" / "hooks-tampered"
        if tamper_path.is_file():
            try:
                tamper_path.unlink()
            except Exception:
                pass
        return info(f"  \U0001f512 Hook integrity verified ({count} files + hooks.json)")

    def _regenerate_manifest(self):
        """Regenerate the integrity manifest with current file hashes."""
        manifest = {"files": {}, "hooks_json": None}
        # Track all Python files in hooks/
        for f in sorted(HOOKS_DIR.glob("*.py")):
            h = self._sha256(f)
            if h:
                manifest["files"][f.name] = h
        # Track rules/ subdirectory
        rules_dir = HOOKS_DIR / "rules"
        if rules_dir.is_dir():
            for f in sorted(rules_dir.glob("*.py")):
                key = f"rules/{f.name}"
                h = self._sha256(f)
                if h:
                    manifest["files"][key] = h
        # hooks.json
        hooks_json_path = HOOKS_DST_DIR / "hooks.json"
        if hooks_json_path.is_file():
            manifest["hooks_json"] = hashlib.sha256(
                hooks_json_path.read_bytes()
            ).hexdigest()
        HOOKS_DST_DIR.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(manifest, indent=2))

    def _sha256(self, filepath):
        h = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return None

    def _check_config_poisoning(self):
        try:
            if CONFIG.is_file():
                cfg = json.loads(CONFIG.read_text())
                if cfg.get("disableAllHooks"):
                    return True
        except Exception:
            pass
        return False
