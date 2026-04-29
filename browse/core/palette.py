"""browse/core/palette.py — Global command palette commands for all pages.

Returns JSON-serializable command dicts consumed by the ninja-keys palette.
Each dict has: id, title, hotkey, handler (string action type), and optional href.
palette.js converts the string handler values into real JS functions at runtime.
"""

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


def get_global_commands() -> list:
    """Return global navigation/help commands for every page.

    handler values:
      "navigate"    — palette.js will do location.href = cmd.href
      "help-modal"  — palette.js will open the keyboard shortcuts modal
      "toggle-dark" — palette.js will invoke window.toggleDark()
    """
    return [
        {
            "id": "nav-home",
            "title": "Home",
            "hotkey": "",
            "handler": "navigate",
            "href": "/",
            "section": "Navigation",
        },
        {
            "id": "nav-search",
            "title": "Search",
            "hotkey": "",
            "handler": "navigate",
            "href": "/search",
            "section": "Navigation",
        },
        {
            "id": "nav-sessions",
            "title": "Sessions",
            "hotkey": "",
            "handler": "navigate",
            "href": "/sessions",
            "section": "Navigation",
        },
        {
            "id": "nav-dashboard",
            "title": "Dashboard",
            "hotkey": "",
            "handler": "navigate",
            "href": "/dashboard",
            "section": "Navigation",
        },
        {
            "id": "nav-live",
            "title": "Live",
            "hotkey": "",
            "handler": "navigate",
            "href": "/live",
            "section": "Navigation",
        },
        {
            "id": "nav-diff",
            "title": "Diff",
            "hotkey": "",
            "handler": "navigate",
            "href": "/diff",
            "section": "Navigation",
        },
        {
            "id": "nav-graph",
            "title": "Knowledge Graph",
            "hotkey": "",
            "handler": "navigate",
            "href": "/graph",
            "section": "Explore",
        },
        {
            "id": "nav-embeddings",
            "title": "Embeddings",
            "hotkey": "",
            "handler": "navigate",
            "href": "/embeddings",
            "section": "Explore",
        },
        {
            "id": "nav-eval",
            "title": "Eval",
            "hotkey": "",
            "handler": "navigate",
            "href": "/eval",
            "section": "Admin",
        },
        {
            "id": "toggle-dark",
            "title": "Toggle dark mode",
            "hotkey": "F8",
            "handler": "toggle-dark",
            "section": "View",
        },
        {
            "id": "help-shortcuts",
            "title": "Help \u2014 Keyboard Shortcuts",
            "hotkey": "?",
            "handler": "help-modal",
            "section": "Help",
        },
    ]
