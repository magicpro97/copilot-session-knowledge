#!/usr/bin/env python3
"""Test shard inventory and policy checks.

Run:
    python3 tests/test_inventory.py
    python3 tests/test_inventory.py --report
    python3 tests/test_inventory.py --json
"""

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
README = REPO / "tests" / "README.md"
EXCLUDE_DIRS = {".git", ".octogent", ".venv", "__pycache__", "venv"}
EXCLUDE_PATH_PARTS = {"fixtures"}
TOP_LIMIT = 30

PASS = 0
FAIL = 0


@dataclass(frozen=True)
class InventoryRow:
    file: str
    subsystem: str
    line_count: int
    runtime_class: str
    protected_surface: str


def discover_test_files(root: Path = REPO) -> list[Path]:
    found: list[Path] = []
    for path in sorted(root.rglob("test_*.py")):
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            rel_parts = path.parts
        if set(rel_parts) & EXCLUDE_DIRS:
            continue
        if EXCLUDE_PATH_PARTS & set(rel_parts):
            continue
        if any(part.startswith(".") for part in rel_parts[:-1]):
            continue
        found.append(path)
    return found


def line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle)


def build_inventory(root: Path = REPO) -> list[InventoryRow]:
    rows = []
    for path in discover_test_files(root):
        rel = path.relative_to(root).as_posix()
        lines = line_count(path)
        subsystem = classify_subsystem(rel)
        rows.append(
            InventoryRow(
                file=rel,
                subsystem=subsystem,
                line_count=lines,
                runtime_class=classify_runtime(rel, lines),
                protected_surface=classify_surface(rel, subsystem),
            )
        )
    return sorted(rows, key=lambda row: row.file)


def classify_subsystem(rel: str) -> str:
    name = Path(rel).name
    if rel == "test_security.py":
        return "root-security"
    if rel == "test_fixes.py":
        return "root-runtime"
    prefix_map = [
        ("test_tentacle_", "tentacle"),
        ("test_hook", "hooks"),
        ("test_browse_", "browse"),
        ("test_sync_", "sync"),
        ("test_profile_", "profile"),
        ("test_skill_", "skills"),
        ("test_trend_scout", "trend-scout"),
        ("test_workflow_", "workflow"),
        ("test_checkpoint_", "checkpoint"),
        ("test_constitution", "constitution"),
        ("test_retrieval", "retrieval"),
        ("test_project_", "project"),
    ]
    for prefix, subsystem in prefix_map:
        if name.startswith(prefix):
            return subsystem
    stem = name.removeprefix("test_").removesuffix(".py")
    return stem.split("_", 1)[0] if stem else "misc"


def classify_runtime(rel: str, lines: int) -> str:
    if rel in {"test_security.py", "test_fixes.py"}:
        return "root-canary"
    if lines >= 3_000:
        return "oversized"
    if lines >= 1_000:
        return "large"
    if lines >= 300:
        return "standard"
    return "small"


def classify_surface(rel: str, subsystem: str) -> str:
    if rel == "test_security.py":
        return "security gate"
    if rel == "test_fixes.py":
        return "runtime regression gate"
    surfaces = {
        "browse": "browse backend/UI",
        "checkpoint": "checkpoint tooling",
        "constitution": "spec governance",
        "hooks": "Copilot hook rules",
        "profile": "agent profiles",
        "project": "project setup/context",
        "retrieval": "knowledge retrieval",
        "skills": "skills ecosystem",
        "sync": "sync runtime",
        "tentacle": "tentacle orchestration",
        "trend-scout": "trend scout",
        "workflow": "workflow health",
    }
    return surfaces.get(subsystem, subsystem)


def top_by_line_count(rows: list[InventoryRow], limit: int = TOP_LIMIT) -> list[InventoryRow]:
    return sorted(rows, key=lambda row: (-row.line_count, row.file))[:limit]


def markdown_table(rows: list[InventoryRow]) -> str:
    lines = [
        "| File | Subsystem | Lines | Runtime class | Protected surface |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| `{row.file}` | {row.subsystem} | {row.line_count} | "
            f"{row.runtime_class} | {row.protected_surface} |"
        )
    return "\n".join(lines)


def format_report(rows: list[InventoryRow]) -> str:
    top_rows = top_by_line_count(rows)
    return "\n\n".join(
        [
            f"# Test shard inventory\n\nTotal test files: {len(rows)}",
            f"## Top {TOP_LIMIT} by line count\n\n{markdown_table(top_rows)}",
            f"## Full inventory\n\n{markdown_table(rows)}",
        ]
    )


def record(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" -- {detail}" if detail else ""))


def run_checks() -> int:
    rows = build_inventory()
    files = [row.file for row in rows]
    top_rows = top_by_line_count(rows)
    report = format_report(rows)
    readme = README.read_text(encoding="utf-8")

    record("inventory discovers test files", len(rows) > TOP_LIMIT, str(len(rows)))
    record("inventory includes root security canary", "test_security.py" in files)
    record("inventory includes root fixes canary", "test_fixes.py" in files)
    record("inventory includes this test", "tests/test_inventory.py" in files)
    record("top 30 has expected size", len(top_rows) == min(TOP_LIMIT, len(rows)))
    record(
        "top 30 sorted by descending line count",
        all(
            top_rows[i].line_count >= top_rows[i + 1].line_count
            for i in range(len(top_rows) - 1)
        ),
    )
    record(
        "inventory rows have required columns",
        all(
            row.file
            and row.subsystem
            and row.line_count > 0
            and row.runtime_class
            and row.protected_surface
            for row in rows
        ),
    )
    record(
        "report lists every discovered test file",
        all(f"`{row.file}`" in report for row in rows),
    )
    record("report includes top 30 section", f"Top {TOP_LIMIT} by line count" in report)
    record("README has test shard inventory section", "## Test shard inventory" in readme)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    rows = build_inventory()

    if "--help" in args or "-h" in args:
        print(__doc__)
        return 0
    if "--json" in args:
        print(json.dumps([asdict(row) for row in rows], indent=2))
        return 0
    if "--report" in args:
        print(format_report(rows))
        return 0
    return run_checks()


if __name__ == "__main__":
    sys.exit(main())
