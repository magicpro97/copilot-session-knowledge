#!/usr/bin/env python3
"""sk repo-map — PageRank-ranked symbol map for AI context injection.

Usage:
    python repo-map.py [<path>]           # Map current dir or <path>
    python repo-map.py --top 20           # Show top 20 symbols
    python repo-map.py --json             # JSON output
    python repo-map.py --format concise   # One-liner per symbol
    python repo-map.py --format full      # Include content snippet
    python repo-map.py --project-id <id>  # Use project_id in code_index
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

_DB_CANDIDATES = [
    Path.home() / ".copilot/knowledge.db",
    Path(__file__).parent / "sessions.db",
    Path.home() / ".copilot/tools/sessions.db",
]


def _db_path() -> Path | None:
    if e := os.environ.get("SK_DB_PATH"):
        p = Path(e)
        return p if p.exists() else None
    for p in _DB_CANDIDATES:
        if p.exists():
            return p
    return None


def _load_from_db(project_id: str) -> list[dict]:
    """Load symbols from code_index table."""
    db_path = _db_path()
    if not db_path:
        return []
    try:
        conn = sqlite3.connect(str(db_path) + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT symbol_name, file_path, language, symbol_type, content_snippet
               FROM code_index WHERE project_id = ? ORDER BY file_path, symbol_name""",
            (project_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        return []


def _scan_files(root: Path, max_files: int = 200) -> list[dict]:
    """Fallback: scan source files with regex to extract symbols."""
    symbols = []
    exts = {".py", ".ts", ".js", ".go", ".rs", ".java"}
    count = 0
    for fpath in sorted(root.rglob("*")):
        if count >= max_files:
            break
        if fpath.suffix not in exts or ".git" in fpath.parts or "node_modules" in fpath.parts:
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        count += 1
        for m in re.finditer(r"^(?:class|def|function|fn|func)\s+(\w+)", text, re.MULTILINE):
            sym_type = "class" if m.group(0).startswith("class") else "function"
            symbols.append(
                {
                    "symbol_name": m.group(1),
                    "file_path": str(fpath.relative_to(root)),
                    "language": fpath.suffix.lstrip("."),
                    "symbol_type": sym_type,
                    "content_snippet": "",
                }
            )
    return symbols


def _build_reference_graph(symbols: list[dict], root: Path) -> dict[str, set[str]]:
    """Build {symbol_name: set_of_symbols_that_reference_it}.

    Heuristic: scan each file for occurrences of other symbols' names.
    """
    defined_in: dict[str, str] = {s["symbol_name"]: s["file_path"] for s in symbols}
    sym_names = set(defined_in.keys())

    file_symbols: dict[str, set[str]] = defaultdict(set)
    for s in symbols:
        file_symbols[s["file_path"]].add(s["symbol_name"])

    # referenced_by[sym] = set of symbols in files that reference sym
    referenced_by: dict[str, set[str]] = defaultdict(set)

    for file_path, file_syms in file_symbols.items():
        full_path = root / file_path
        try:
            text = full_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for sym in sym_names:
            if sym in file_syms:
                continue  # skip self-references
            if re.search(r"\b" + re.escape(sym) + r"\b", text):
                for fs in file_syms:
                    referenced_by[sym].add(fs)

    return referenced_by


def _pagerank(
    referenced_by: dict[str, set[str]],
    sym_names: list[str],
    iterations: int = 15,
    damping: float = 0.85,
) -> dict[str, float]:
    """Pure-stdlib PageRank by power iteration.

    referenced_by[s] = set of symbols that point TO s (in-links).
    """
    n = len(sym_names)
    if n == 0:
        return {}
    ranks: dict[str, float] = {s: 1.0 / n for s in sym_names}

    # Build out-links: sym -> [syms it references]
    out_links: dict[str, list[str]] = defaultdict(list)
    for sym, referrers in referenced_by.items():
        for ref in referrers:
            out_links[ref].append(sym)

    for _ in range(iterations):
        new_ranks: dict[str, float] = {}
        for sym in sym_names:
            in_sum = 0.0
            for ref in referenced_by.get(sym, set()):
                out_count = len(out_links.get(ref, [])) or 1
                in_sum += ranks.get(ref, 0.0) / out_count
            new_ranks[sym] = (1 - damping) / n + damping * in_sum

        diff = sum(abs(new_ranks.get(s, 0) - ranks.get(s, 0)) for s in sym_names)
        ranks = new_ranks
        if diff < 1e-6:
            break

    return ranks


def _format_map(symbols: list[dict], ranks: dict[str, float], top: int, fmt: str) -> str:
    """Format the repo map as a concise string."""
    ranked_syms = sorted(
        symbols,
        key=lambda s: (-ranks.get(s["symbol_name"], 0.0), s["symbol_name"]),
    )[:top]

    if fmt == "concise":
        lines = []
        for s in ranked_syms:
            score = ranks.get(s["symbol_name"], 0.0)
            lines.append(f"{s['file_path']}:{s['symbol_name']} [{s.get('symbol_type', '?')}] score={score:.4f}")
        return "\n".join(lines)
    else:  # full
        lines = []
        for s in ranked_syms:
            score = ranks.get(s["symbol_name"], 0.0)
            snippet = s.get("content_snippet", "")[:80].replace("\n", " ")
            lines.append(f"{s['file_path']}:{s['symbol_name']} [{s.get('symbol_type', '?')}] score={score:.4f}")
            if snippet:
                lines.append(f"  {snippet}")
        return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="PageRank-ranked symbol map for AI context")
    parser.add_argument("path", nargs="?", default=".", help="Root directory to map")
    parser.add_argument("--top", type=int, default=30, help="Number of top symbols to show")
    parser.add_argument(
        "--project-id",
        dest="project_id",
        default="",
        help="project_id in code_index table (default: auto-detect from dir)",
    )
    parser.add_argument("--format", choices=["concise", "full"], default="concise")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--iterations", type=int, default=15, help="PageRank iterations")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"Path not found: {root}", file=sys.stderr)
        sys.exit(1)

    project_id = args.project_id or root.name

    symbols = _load_from_db(project_id)
    source = "code_index"
    if not symbols:
        symbols = _scan_files(root)
        source = "filesystem"

    if not symbols:
        if args.as_json:
            print(json.dumps({"error": "No symbols found", "path": str(root)}))
        else:
            print(f"No symbols found in {root}. Run 'sk code-index {root}' first.")
        return

    referenced_by = _build_reference_graph(symbols, root)
    sym_names = list({s["symbol_name"] for s in symbols})
    ranks = _pagerank(referenced_by, sym_names, iterations=args.iterations)

    if args.as_json:
        ranked = sorted(symbols, key=lambda s: (-ranks.get(s["symbol_name"], 0.0), s["symbol_name"]))[: args.top]
        output = {
            "root": str(root),
            "source": source,
            "total_symbols": len(symbols),
            "top": args.top,
            "symbols": [{**s, "pagerank_score": round(ranks.get(s["symbol_name"], 0.0), 6)} for s in ranked],
        }
        print(json.dumps(output, indent=2))
        return

    print(f"Repository map: {root.name} ({len(symbols)} symbols, source={source})")
    print("━" * 50)
    text = _format_map(symbols, ranks, top=args.top, fmt=args.format)
    print(text)
    print(f"\n[{args.top} of {len(symbols)} total symbols shown, ranked by PageRank]")


if __name__ == "__main__":
    main()
