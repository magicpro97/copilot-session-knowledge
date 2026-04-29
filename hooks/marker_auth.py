#!/usr/bin/env python3
"""marker_auth.py — HMAC-signed marker authentication.

Markers are JSON files with name, timestamp, and HMAC-SHA256 signature.
Secret stored in ~/.copilot/hooks/.marker-secret (protected by OS immutable flags).

If secret doesn't exist, falls back to simple file existence check (backward compat).
Once secret exists (after --lock-hooks), unsigned markers are rejected.
"""

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

SECRET_PATH = Path.home() / ".copilot" / "hooks" / ".marker-secret"
MARKERS_DIR = Path.home() / ".copilot" / "markers"

PROTECTED_PATTERNS = (
    ".marker-secret",
    "integrity-manifest",
    "marker_auth.py",
    "marker_auth ",
    ".copilot/hooks/.",
)


def _read_secret():
    try:
        if SECRET_PATH.is_file():
            return SECRET_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None


def sign_marker(marker_path, name):
    MARKERS_DIR.mkdir(parents=True, exist_ok=True)
    secret = _read_secret()
    if not secret:
        marker_path.touch()
        return
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{name}:{ts}".encode(), hashlib.sha256).hexdigest()
    marker_path.write_text(json.dumps({"name": name, "ts": ts, "sig": sig}), encoding="utf-8")


def verify_marker(marker_path, name):
    if not marker_path.is_file():
        return False
    secret = _read_secret()
    if not secret:
        return True
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        m_name = data.get("name", "")
        ts = data.get("ts", "")
        sig = data.get("sig", "")
        if m_name != name:
            return False
        expected = hmac.new(secret.encode(), f"{name}:{ts}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except (json.JSONDecodeError, KeyError, TypeError):
        return False


def sign_counter(counter_path, value):
    MARKERS_DIR.mkdir(parents=True, exist_ok=True)
    secret = _read_secret()
    if not secret:
        counter_path.write_text(str(value), encoding="utf-8")
        return
    name = counter_path.name
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{name}:{value}:{ts}".encode(), hashlib.sha256).hexdigest()
    counter_path.write_text(json.dumps({"name": name, "value": value, "ts": ts, "sig": sig}), encoding="utf-8")


def verify_counter(counter_path):
    if not counter_path.is_file():
        return 0
    secret = _read_secret()
    content = counter_path.read_text(encoding="utf-8").strip()
    if not secret:
        try:
            return int(content)
        except (ValueError, TypeError):
            return 0
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            return 0
        name = data.get("name", "")
        value = data.get("value", 0)
        ts = data.get("ts", "")
        sig = data.get("sig", "")
        expected = hmac.new(secret.encode(), f"{name}:{value}:{ts}".encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected):
            return int(value)
        return 0
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
        return 0


def sign_list_marker(marker_path, lines):
    MARKERS_DIR.mkdir(parents=True, exist_ok=True)
    content = "\n".join(sorted(lines))
    secret = _read_secret()
    if not secret:
        marker_path.write_text(content, encoding="utf-8")
        return
    name = marker_path.name
    sig = hmac.new(secret.encode(), f"{name}:{content}".encode(), hashlib.sha256).hexdigest()
    marker_path.write_text(json.dumps({"name": name, "content": content, "sig": sig}), encoding="utf-8")


def verify_list_marker(marker_path):
    if not marker_path.is_file():
        return set()
    secret = _read_secret()
    raw = marker_path.read_text(encoding="utf-8").strip()
    if not secret:
        return set(raw.splitlines()) if raw else set()
    try:
        data = json.loads(raw)
        content = data.get("content", "")
        sig = data.get("sig", "")
        name = data.get("name", "")
        expected = hmac.new(secret.encode(), f"{name}:{content}".encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected):
            return set(content.splitlines()) if content else set()
        return set()
    except (json.JSONDecodeError, KeyError, TypeError):
        return set()


def is_secret_access(command):
    for pattern in PROTECTED_PATTERNS:
        if pattern in command:
            return True
    return False


def check_tamper_marker():
    tamper_path = MARKERS_DIR / "hooks-tampered"
    return verify_marker(tamper_path, "hooks-tampered")


def create_tamper_marker():
    tamper_path = MARKERS_DIR / "hooks-tampered"
    sign_marker(tamper_path, "hooks-tampered")


if __name__ == "__main__":
    # Only gen-secret (used by install.py) and verify (diagnostics) are allowed.
    # sign command REMOVED — agents must not forge markers.
    if len(sys.argv) < 2:
        print("Usage: python3 marker_auth.py gen-secret")
        print("       python3 marker_auth.py verify <name>")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "gen-secret":
        import secrets

        s = secrets.token_hex(32)
        SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECRET_PATH.write_text(s, encoding="utf-8")
        print(f"Secret generated: {SECRET_PATH}")
        sys.exit(0)
    elif cmd == "verify":
        if len(sys.argv) < 3:
            print("Need marker name")
            sys.exit(1)
        name = sys.argv[2]
        marker = MARKERS_DIR / name
        valid = verify_marker(marker, name)
        print(f"{'Valid' if valid else 'INVALID'}: {marker}")
        sys.exit(0 if valid else 1)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
