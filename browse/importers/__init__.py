"""browse/importers — Read-only external source importers for debug log entries.

WBS-106: VS Code Agent Debug Log JSONL and OTel ReadableSpan JSONL importers.

Each importer normalises source-specific shapes to the BrowseDebugEntry schema
defined in docs/DEBUG-LOG-CONTRACT.md, applies
``browse.core.redaction.redact_entry``, and returns a ``(entries, summary)``
tuple.  No writes to the debug-log DB occur inside the importers; callers
decide whether to persist the entries.

Importers
---------
- ``browse.importers.vscode_agent_debug_log``  VS Code ``IDebugLogEntry`` JSONL
- ``browse.importers.otel_file``               OTel ``ReadableSpan`` JSONL
"""

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
