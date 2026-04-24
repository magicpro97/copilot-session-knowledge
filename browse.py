#!/usr/bin/env python3
"""browse.py — thin shim; real implementation lives in the browse/ package."""
# Re-export package symbols so existing code/tests using 'import browse' still work.
from browse import main, _make_handler_class, _sanitize_fts_query, _esc, _SESSION_ID_RE  # noqa: F401

if __name__ == "__main__":
    main()
