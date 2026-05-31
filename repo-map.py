#!/usr/bin/env python3
"""sk repo-map — PageRank-ranked symbol map for AI context injection.

Usage:
    python repo-map.py [<path>]             # Map current dir or <path>
    python repo-map.py --top 20             # Show top 20 symbols
    python repo-map.py --json               # JSON output
    python repo-map.py --format markdown    # Markdown output
    python repo-map.py --format full        # Include content snippet
    python repo-map.py --project-id <id>    # Use project_id in code_index
    python repo-map.py --tokens 4000        # Approximate output token budget
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DEFAULT_DB_PATH = SESSION_STATE / "knowledge.db"
EXTENSIONS = {".py", ".ts", ".js", ".go", ".rs", ".java"}
IDENTIFIER_RE = re.compile(r"\b[A-Za-z_]\w+\b")
SYMBOL_DEF_RE = re.compile(r"^(?:class|def|function|fn|func)\s+(\w+)", re.MULTILINE)

# TODO(#742): cache by file content hash for incremental updates
# TODO(#742): MCP tool registration in mcp-server.py


def _db_path() -> Path | None:
    if db_env := os.environ.get("SK_DB_PATH"):
        db_path = Path(db_env).expanduser().resolve()
        return db_path if db_path.exists() else None
    return DEFAULT_DB_PATH if DEFAULT_DB_PATH.exists() else None


def _make_project_id(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]


def _load_from_db(project_id: str) -> list[dict]:
    """Load symbols from code_index table."""
    db_path = _db_path()
    if not db_path:
        return []
    try:
        with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as conn:
            conn.execute("PRAGMA query_only = ON")
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT symbol_name, file_path, language, symbol_kind, content_snippet
                   FROM code_index WHERE project_id = ? ORDER BY file_path, symbol_name""",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except (sqlite3.OperationalError, sqlite3.DatabaseError, ValueError):
        return []


def _scan_files(root: Path, max_files: int = 200) -> list[dict]:
    """Fallback: scan source files with regex to extract symbols."""
    symbols = []
    count = 0
    for fpath in root.rglob("*"):
        if count >= max_files:
            break
        if not fpath.is_file() or fpath.suffix not in EXTENSIONS:
            continue
        if ".git" in fpath.parts or "node_modules" in fpath.parts:
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        count += 1
        for match in SYMBOL_DEF_RE.finditer(text):
            symbol_kind = "class" if match.group(0).startswith("class") else "function"
            symbols.append(
                {
                    "symbol_name": match.group(1),
                    "file_path": str(fpath.relative_to(root)),
                    "language": fpath.suffix.lstrip("."),
                    "symbol_kind": symbol_kind,
                    "content_snippet": "",
                }
            )
    return symbols


def _build_reference_graph(symbols: list[dict], root: Path) -> dict[str, set[str]]:
    """Build {symbol_name: set_of_symbols_that_reference_it}."""
    sym_names = {symbol["symbol_name"] for symbol in symbols}

    file_symbols: dict[str, set[str]] = defaultdict(set)
    for symbol in symbols:
        file_symbols[symbol["file_path"]].add(symbol["symbol_name"])

    referenced_by: dict[str, set[str]] = defaultdict(set)
    for file_path, file_syms in file_symbols.items():
        full_path = root / file_path
        try:
            text = full_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        referenced_symbols = IDENTIFIER_RE.findall(text)
        for sym in set(referenced_symbols) & sym_names:
            if sym in file_syms:
                continue
            for file_symbol in file_syms:
                referenced_by[sym].add(file_symbol)

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

        diff = sum(abs(new_ranks.get(s, 0.0) - ranks.get(s, 0.0)) for s in sym_names)
        ranks = new_ranks
        if diff < 1e-6:
            break

    return ranks


def _ranked_symbols(symbols: list[dict], ranks: dict[str, float], top: int) -> list[dict]:
    return sorted(
        symbols,
        key=lambda symbol: (-ranks.get(symbol["symbol_name"], 0.0), symbol["symbol_name"]),
    )[:top]


def _format_symbol_line(symbol: dict, score: float) -> str:
    symbol_kind = symbol.get("symbol_kind", "?")
    return f"{symbol['file_path']}:{symbol['symbol_name']} [{symbol_kind}] score={score:.4f}"


def _format_map(symbols: list[dict], ranks: dict[str, float], top: int, fmt: str) -> str:
    """Format the repo map."""
    ranked_syms = _ranked_symbols(symbols, ranks, top=top)

    if fmt == "markdown":
        lines = ["## Top symbols", ""]
        for index, symbol in enumerate(ranked_syms, start=1):
            score = ranks.get(symbol["symbol_name"], 0.0)
            lines.append(
                f"{index}. `{symbol['symbol_name']}` — `{symbol['file_path']}` "
                f"({symbol.get('symbol_kind', '?')}, score={score:.4f})"
            )
            snippet = symbol.get("content_snippet", "")[:80].replace("\n", " ").strip()
            if snippet:
                lines.append(f"   - Snippet: `{snippet}`")
        return "\n".join(lines)

    lines = []
    for symbol in ranked_syms:
        score = ranks.get(symbol["symbol_name"], 0.0)
        lines.append(_format_symbol_line(symbol, score))
        if fmt == "full":
            snippet = symbol.get("content_snippet", "")[:80].replace("\n", " ").strip()
            if snippet:
                lines.append(f"  {snippet}")
    return "\n".join(lines)


def _truncate_output(text: str, tokens: int) -> str:
    if tokens <= 0:
        return text
    max_chars = tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + f"\n\n… [truncated to ~{tokens} tokens]"


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
    parser.add_argument("--format", choices=["markdown", "concise", "full"], default="markdown")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--iterations", type=int, default=15, help="PageRank iterations")
    parser.add_argument(
        "--tokens",
        type=int,
        default=4000,
        help="Approximate output token budget for non-JSON formats",
    )
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"Path not found: {root}", file=sys.stderr)
        sys.exit(1)

    project_id = args.project_id or _make_project_id(root)

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
    sym_names = sorted({symbol["symbol_name"] for symbol in symbols})
    ranks = _pagerank(referenced_by, sym_names, iterations=args.iterations)

    if args.as_json:
        ranked = _ranked_symbols(symbols, ranks, top=args.top)
        output = {
            "root": str(root),
            "source": source,
            "total_symbols": len(symbols),
            "top": args.top,
            "symbols": [
                {**symbol, "pagerank_score": round(ranks.get(symbol["symbol_name"], 0.0), 6)} for symbol in ranked
            ],
        }
        print(json.dumps(output, indent=2))
        return

    header = [
        f"# Repository map: {root.name}",
        "",
        f"- Source: `{source}`",
        f"- Project ID: `{project_id}`",
        f"- Total symbols: `{len(symbols)}`",
        f"- Showing: top `{args.top}` ranked symbols",
        "",
    ]
    body = _format_map(symbols, ranks, top=args.top, fmt=args.format)
    footer = f"\n\n[{args.top} of {len(symbols)} total symbols shown, ranked by PageRank]"
    print(_truncate_output("\n".join(header) + body + footer, args.tokens))


if __name__ == "__main__":
    main()
