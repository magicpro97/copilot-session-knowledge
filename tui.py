#!/usr/bin/env python3
"""sk tui — interactive terminal knowledge browser (curses)."""

import os
import sqlite3
import sys
import textwrap
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

# curses is not available on Windows without windows-curses
try:
    import curses
except ImportError:
    print("sk tui: curses is not available on this platform.", file=sys.stderr)
    print("  On Windows, use `sk browse` instead or install `windows-curses`.", file=sys.stderr)
    sys.exit(1)


def _load_entries(db_path, category=None, limit=200):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    q = "SELECT id, category, title, confidence, content FROM knowledge_entries"
    clauses = ["deleted_at IS NULL"]
    params: list = []
    if category:
        clauses.append("category=?")
        params.append(category)
    q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY confidence DESC, last_seen DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows


CATEGORY_KEYS = {
    "m": "mistake",
    "p": "pattern",
    "d": "decision",
    "t": "tool",
    "f": "feature",
    "r": "refactor",
    "y": "discovery",
    "a": "all",
}
CATEGORY_COLORS = {"mistake": 1, "pattern": 2, "decision": 3, "tool": 4, "feature": 5, "refactor": 6, "discovery": 7}


def _run_tui(stdscr, db_path):
    curses.curs_set(0)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
    curses.init_pair(6, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(7, curses.COLOR_BLUE, curses.COLOR_BLACK)

    category = None
    entries = _load_entries(db_path, category)
    selected = 0
    scroll = 0
    detail_mode = False

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        if detail_mode and entries:
            e = entries[selected]
            stdscr.addstr(0, 0, f"#{e[0]} [{e[1]}] {e[2][: w - 20]}  conf={e[3]:.2f}", curses.A_BOLD)
            lines = textwrap.wrap(e[4], w - 2)
            for i, line in enumerate(lines[: h - 3], 1):
                stdscr.addstr(i, 0, line)
            stdscr.addstr(h - 1, 0, " ESC=back  q=quit", curses.A_REVERSE)
        else:
            stdscr.addstr(
                0,
                0,
                f"sk knowledge browser  [{len(entries)} entries]  filter: {category or 'all'}",
                curses.A_BOLD,
            )
            stdscr.addstr(
                1,
                0,
                " m=mistakes p=patterns d=decisions t=tools f=features r=refactor y=discovery a=all  ENTER=expand  q=quit",
                curses.A_DIM,
            )
            visible = h - 3
            for i, e in enumerate(entries[scroll : scroll + visible], 0):
                idx = scroll + i
                prefix = "▶ " if idx == selected else "  "
                line = f"{prefix}#{e[0]} [{e[1][:3]}] {e[2][: w - 25]}  {e[3]:.1f}"
                color = CATEGORY_COLORS.get(e[1], 0)
                attr = curses.color_pair(color) | (curses.A_REVERSE if idx == selected else 0)
                try:
                    stdscr.addstr(i + 2, 0, line[: w - 1], attr)
                except curses.error:
                    pass

        stdscr.refresh()
        key = stdscr.getch()

        if detail_mode:
            if key in (27, ord("q"), ord(" ")):
                detail_mode = False
        else:
            if key == curses.KEY_DOWN and selected < len(entries) - 1:
                selected += 1
                if selected >= scroll + (h - 3):
                    scroll += 1
            elif key == curses.KEY_UP and selected > 0:
                selected -= 1
                if selected < scroll:
                    scroll -= 1
            elif key in (ord("\n"), curses.KEY_ENTER):
                detail_mode = True
            elif key == ord("q"):
                break
            elif 0 <= key < 256 and chr(key) in CATEGORY_KEYS:
                cat = CATEGORY_KEYS[chr(key)]
                category = None if cat == "all" else cat
                entries = _load_entries(db_path, category)
                selected = scroll = 0


def main():
    import argparse

    p = argparse.ArgumentParser(description="Interactive terminal knowledge browser")
    p.add_argument("--db", help="Path to knowledge.db")
    args = p.parse_args()
    db_path = (
        args.db or os.environ.get("SK_DB_PATH") or str(Path.home() / ".copilot" / "session-state" / "knowledge.db")
    )
    curses.wrapper(_run_tui, db_path)


if __name__ == "__main__":
    main()
