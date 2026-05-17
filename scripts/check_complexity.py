#!/usr/bin/env python3
"""check_complexity.py - stdlib-only Python complexity reporter.

Reports file line counts, function line counts, and approximate cyclomatic
complexity for targeted Python files/directories. Warnings are advisory: valid
Python files with high complexity still exit 0. Missing paths and parse errors
exit non-zero because the report could not be produced.

Usage:
    python3 scripts/check_complexity.py
    python3 scripts/check_complexity.py tentacle.py
    python3 scripts/check_complexity.py --json browse/
"""

import argparse
import ast
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
EXCLUDE_DIRS = {".octogent", "__pycache__", ".git", ".venv", "venv", "node_modules"}

FUNCTION_COMPLEXITY_WARNING = 15
FUNCTION_COMPLEXITY_HIGH = 25
FUNCTION_LINES_WARNING = 50
FUNCTION_LINES_HIGH = 100
FILE_LINES_WARNING = 400
FILE_LINES_HIGH = 800

SEVERITY_RANK = {"ok": 0, "warning": 1, "high": 2}


@dataclass
class FunctionMetric:
    name: str
    line: int
    end_line: int
    lines: int
    complexity: int
    severity: str


@dataclass
class FileMetric:
    path: str
    lines: int
    functions: list[FunctionMetric]
    severity: str


class ComplexityVisitor(ast.NodeVisitor):
    """Compute a simple cyclomatic complexity score for one function body."""

    def __init__(self) -> None:
        self.complexity = 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return None

    def visit_If(self, node: ast.If) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.complexity += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.complexity += 1 + len(node.ifs)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.complexity += len(node.cases)
        self.generic_visit(node)


class FunctionCollector(ast.NodeVisitor):
    """Collect function metrics while preserving class/nested names."""

    def __init__(self) -> None:
        self.functions: list[FunctionMetric] = []
        self._scope: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        visitor = ComplexityVisitor()
        for child in node.body:
            visitor.visit(child)

        end_line = getattr(node, "end_lineno", node.lineno)
        line_count = max(1, end_line - node.lineno + 1)
        qualified_name = ".".join([*self._scope, node.name])
        self.functions.append(
            FunctionMetric(
                name=qualified_name,
                line=node.lineno,
                end_line=end_line,
                lines=line_count,
                complexity=visitor.complexity,
                severity=max_severity(
                    severity_for(visitor.complexity, FUNCTION_COMPLEXITY_WARNING, FUNCTION_COMPLEXITY_HIGH),
                    severity_for(line_count, FUNCTION_LINES_WARNING, FUNCTION_LINES_HIGH),
                ),
            )
        )

        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()


def severity_for(value: int, warning_threshold: int, high_threshold: int) -> str:
    if value > high_threshold:
        return "high"
    if value > warning_threshold:
        return "warning"
    return "ok"


def max_severity(*values: str) -> str:
    return max(values, key=lambda item: SEVERITY_RANK[item])


def path_label(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(path)


def iter_py_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix == ".py" else []

    files: list[Path] = []
    for entry in sorted(target.rglob("*.py")):
        try:
            rel_parts = entry.relative_to(target).parts
        except ValueError:
            rel_parts = entry.parts
        if set(rel_parts) & EXCLUDE_DIRS:
            continue
        if "fixtures" in rel_parts[:-1]:
            continue
        if any(part.startswith(".") for part in rel_parts[:-1]):
            continue
        files.append(entry)
    return files


def default_targets() -> list[Path]:
    targets = sorted(path for path in REPO.iterdir() if path.is_file() and path.suffix == ".py")
    for name in ("browse", "hooks", "scripts"):
        path = REPO / name
        if path.exists():
            targets.append(path)
    return targets


def resolve_targets(args: list[str]) -> tuple[list[Path], list[str]]:
    raw_targets = [Path(arg) for arg in args] if args else default_targets()
    targets: list[Path] = []
    errors: list[str] = []
    for target in raw_targets:
        resolved = target if target.is_absolute() else (Path.cwd() / target)
        if not resolved.exists():
            errors.append(f"{target}: path does not exist")
            continue
        targets.append(resolved)
    return targets, errors


def analyze_file(path: Path) -> FileMetric:
    text = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text, filename=str(path))
    collector = FunctionCollector()
    collector.visit(tree)
    line_count = len(text.splitlines())
    file_severity = severity_for(line_count, FILE_LINES_WARNING, FILE_LINES_HIGH)
    function_severity = max_severity(*(fn.severity for fn in collector.functions), "ok")
    return FileMetric(
        path=path_label(path),
        lines=line_count,
        functions=collector.functions,
        severity=max_severity(file_severity, function_severity),
    )


def build_report(targets: list[Path]) -> tuple[list[FileMetric], list[str]]:
    files: list[Path] = []
    for target in targets:
        files.extend(iter_py_files(target))
    files = sorted(set(files), key=lambda item: path_label(item))

    metrics: list[FileMetric] = []
    errors: list[str] = []
    for path in files:
        try:
            metrics.append(analyze_file(path))
        except SyntaxError as exc:
            errors.append(f"{path_label(path)}:{exc.lineno}: SyntaxError: {exc.msg}")
        except ValueError as exc:
            errors.append(f"{path_label(path)}: {exc}")
        except OSError as exc:
            errors.append(f"{path_label(path)}: {exc}")
    return metrics, errors


def summarize(files: list[FileMetric]) -> dict:
    functions = [fn for file in files for fn in file.functions]
    return {
        "files_checked": len(files),
        "functions_checked": len(functions),
        "warning_files": sum(1 for file in files if file.severity == "warning"),
        "high_files": sum(1 for file in files if file.severity == "high"),
        "warning_functions": sum(1 for fn in functions if fn.severity == "warning"),
        "high_functions": sum(1 for fn in functions if fn.severity == "high"),
    }


def thresholds() -> dict:
    return {
        "function_complexity": {
            "warning": FUNCTION_COMPLEXITY_WARNING,
            "high": FUNCTION_COMPLEXITY_HIGH,
        },
        "function_lines": {
            "warning": FUNCTION_LINES_WARNING,
            "high": FUNCTION_LINES_HIGH,
        },
        "file_lines": {
            "warning": FILE_LINES_WARNING,
            "high": FILE_LINES_HIGH,
        },
    }


def report_dict(files: list[FileMetric], errors: list[str]) -> dict:
    return {
        "summary": summarize(files),
        "thresholds": thresholds(),
        "files": [
            {
                **asdict(file),
                "functions": [asdict(fn) for fn in file.functions],
            }
            for file in files
        ],
        "errors": errors,
    }


def print_text_report(files: list[FileMetric], errors: list[str]) -> None:
    summary = summarize(files)
    print("Complexity report")
    print(f"Files checked: {summary['files_checked']} | Functions checked: {summary['functions_checked']}")
    print(
        "Thresholds: "
        f"function complexity >{FUNCTION_COMPLEXITY_WARNING}/>{FUNCTION_COMPLEXITY_HIGH}, "
        f"function lines >{FUNCTION_LINES_WARNING}/>{FUNCTION_LINES_HIGH}, "
        f"file lines >{FILE_LINES_WARNING}/>{FILE_LINES_HIGH}"
    )
    print()

    for file in files:
        print(f"{file.path}: lines={file.lines} functions={len(file.functions)} severity={file.severity}")
        for fn in file.functions:
            if fn.severity == "ok":
                continue
            print(f"  {fn.severity}: {fn.name} line={fn.line} complexity={fn.complexity} lines={fn.lines}")

    if errors:
        print("\nErrors:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
    elif not files:
        print("No Python files found.")
    elif not any(file.severity != "ok" for file in files):
        print("\nNo complexity advisories.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Report Python complexity metrics.")
    parser.add_argument("paths", nargs="*", help="Python files or directories to inspect")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    targets, target_errors = resolve_targets(args.paths)
    files, report_errors = build_report(targets)
    errors = [*target_errors, *report_errors]

    if args.json:
        print(json.dumps(report_dict(files, errors), indent=2, sort_keys=True))
    else:
        print_text_report(files, errors)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
