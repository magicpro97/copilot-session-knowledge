"""harness/manifest.py — Load and cache the harness command manifest.

Reads ``harness-manifest.json`` once per process (lru_cache) and returns
``{"version": 1, "commands": {...}}``.  Fail-open: any read or parse error
returns an empty commands dict rather than raising.
"""

import json
import os
import sys
from functools import lru_cache
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_MANIFEST_FILENAME = "harness-manifest.json"
_EMPTY: dict = {"version": 1, "commands": {}}


@lru_cache(maxsize=1)
def load_manifest(tools_dir: str = "") -> dict:
    """Return parsed manifest dict, cached after the first call.

    Args:
        tools_dir: Directory containing ``harness-manifest.json``.
                   Defaults to the directory containing this file.

    Returns:
        Dict with keys ``version`` (int) and ``commands`` (dict).
        On any error, returns ``{"version": 1, "commands": {}}``.
    """
    base = Path(tools_dir) if tools_dir else Path(__file__).parent.parent
    manifest_path = base / _MANIFEST_FILENAME
    try:
        with manifest_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "commands" not in data:
            return _EMPTY.copy()
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _EMPTY.copy()
