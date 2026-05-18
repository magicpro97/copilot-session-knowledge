#!/usr/bin/env python3
"""Functional tests for wbs-issue-gen.py (issue #425).

Run:
    python tests/test_wbs_issue_gen.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
SCRIPT = REPO / "wbs-issue-gen.py"


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" - {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Helper: import core functions from the script without executing main()
# ---------------------------------------------------------------------------


def _import_wbs() -> object:
    import importlib.util

    spec = importlib.util.spec_from_file_location("wbs_issue_gen", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_script_file_exists() -> None:
    test("wbs-issue-gen.py exists at repo root", SCRIPT.is_file())


def test_schema_output() -> None:
    """--schema prints the field reference and exits 0."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--schema"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    test("--schema exits 0", result.returncode == 0, result.stderr[:200])
    test("--schema output contains REQUIRED section", "REQUIRED:" in result.stdout)
    test("--schema output contains OPTIONAL section", "OPTIONAL:" in result.stdout)
    test("--schema output mentions 'title'", "title" in result.stdout)
    test("--schema output mentions 'context'", "context" in result.stdout)
    test("--schema output says context is NOT sent to GitHub", "NOT sent to GitHub" in result.stdout)
    test("--schema output mentions 'priority'", "priority" in result.stdout)


def test_generate_jsonl_stdout() -> None:
    """--generate N writes valid JSONL to stdout."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--generate", "3"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    test("--generate exits 0", result.returncode == 0, result.stderr[:200])
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    test("--generate 3 produces 3 lines", len(lines) == 3, f"got {len(lines)}")
    if lines:
        for idx, line in enumerate(lines, 1):
            try:
                entry = json.loads(line)
                test(f"generated line {idx} is valid JSON", True)
                test(f"generated line {idx} has 'title'", "title" in entry)
                test(f"generated line {idx} has 'priority'", "priority" in entry)
                test(f"generated line {idx} has 'context'", "context" in entry)
            except json.JSONDecodeError as exc:
                test(f"generated line {idx} is valid JSON", False, str(exc))


def test_generate_jsonl_to_file() -> None:
    """--generate N --output FILE writes JSONL to a file."""
    with tempfile.TemporaryDirectory(prefix="wbs-test-") as tmp:
        outfile = Path(tmp) / "out.jsonl"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--generate", "5", "--output", str(outfile)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        test("--generate --output exits 0", result.returncode == 0, result.stderr[:200])
        test("output file exists", outfile.is_file())
        if outfile.is_file():
            lines = [l for l in outfile.read_text(encoding="utf-8").splitlines() if l.strip()]
            test("output file has 5 lines", len(lines) == 5, f"got {len(lines)}")


def test_validate_valid_jsonl() -> None:
    """--validate accepts a well-formed JSONL file."""
    with tempfile.TemporaryDirectory(prefix="wbs-test-") as tmp:
        valid_file = Path(tmp) / "valid.jsonl"
        entries = [
            {"title": "WBS-001: First issue"},
            {
                "title": "WBS-002: Second issue",
                "body": "Some body",
                "labels": ["wbs", "p1"],
                "priority": "P1",
                "context": {"wave": "2b"},
            },
        ]
        valid_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--validate", str(valid_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        test("--validate valid JSONL exits 0", result.returncode == 0, result.stderr[:300])
        test(
            "--validate valid JSONL reports OK",
            "Validation OK" in result.stdout,
            f"stdout={result.stdout[:200]}",
        )


def test_validate_rejects_missing_title() -> None:
    """--validate rejects entries without required 'title' field."""
    with tempfile.TemporaryDirectory(prefix="wbs-test-") as tmp:
        bad_file = Path(tmp) / "bad.jsonl"
        bad_file.write_text(json.dumps({"body": "no title here"}) + "\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--validate", str(bad_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        test("--validate missing title exits non-zero", result.returncode != 0)
        test(
            "--validate missing title reports error",
            "missing required fields" in result.stderr or "ERROR" in result.stderr,
            f"stderr={result.stderr[:300]}",
        )


def test_validate_rejects_invalid_priority() -> None:
    """--validate rejects entries with an invalid priority value."""
    with tempfile.TemporaryDirectory(prefix="wbs-test-") as tmp:
        bad_file = Path(tmp) / "bad_priority.jsonl"
        bad_file.write_text(
            json.dumps({"title": "WBS-001: Test", "priority": "HIGH"}) + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--validate", str(bad_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        test("--validate invalid priority exits non-zero", result.returncode != 0)
        test(
            "--validate invalid priority reports error",
            "priority" in result.stderr,
            f"stderr={result.stderr[:300]}",
        )


def test_validate_rejects_unknown_fields() -> None:
    """--validate rejects entries with fields not in the schema."""
    with tempfile.TemporaryDirectory(prefix="wbs-test-") as tmp:
        bad_file = Path(tmp) / "bad_field.jsonl"
        bad_file.write_text(
            json.dumps({"title": "WBS-001: Test", "bogus_field": "oops"}) + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--validate", str(bad_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        test("--validate unknown field exits non-zero", result.returncode != 0)
        test(
            "--validate unknown field reports error",
            "unknown fields" in result.stderr or "ERROR" in result.stderr,
            f"stderr={result.stderr[:300]}",
        )


def test_validate_rejects_invalid_json() -> None:
    """--validate rejects lines that are not valid JSON."""
    with tempfile.TemporaryDirectory(prefix="wbs-test-") as tmp:
        bad_file = Path(tmp) / "bad_json.jsonl"
        bad_file.write_text("not json at all\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--validate", str(bad_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        test("--validate invalid JSON exits non-zero", result.returncode != 0)
        test(
            "--validate invalid JSON reports parse error",
            "JSON parse error" in result.stderr or "ERROR" in result.stderr,
            f"stderr={result.stderr[:300]}",
        )


def test_context_not_in_github_payload() -> None:
    """_github_payload() excludes 'context' and 'priority' from the REST API payload."""
    mod = _import_wbs()
    entry = {
        "title": "WBS-001: Test issue",
        "body": "Some body",
        "labels": ["wbs"],
        "priority": "P1",
        "context": {"wave": "2b", "wbs_id": "WBS-001"},
        "assignees": ["octocat"],
        "milestone": "Wave 2",
    }
    payload = mod._github_payload(entry)
    test("_github_payload includes 'title'", "title" in payload)
    test("_github_payload includes 'body'", "body" in payload)
    test("_github_payload includes 'labels'", "labels" in payload)
    test("_github_payload includes 'assignees'", "assignees" in payload)
    test("_github_payload includes 'milestone'", "milestone" in payload)
    test(
        "_github_payload excludes 'context' (never sent to GitHub)",
        "context" not in payload,
        f"payload keys: {sorted(payload.keys())}",
    )
    test(
        "_github_payload excludes 'priority' (set via GraphQL, not REST)",
        "priority" not in payload,
        f"payload keys: {sorted(payload.keys())}",
    )


def test_dry_run_output() -> None:
    """--dry-run prints payloads without 'context' or 'priority', exits 0."""
    with tempfile.TemporaryDirectory(prefix="wbs-test-") as tmp:
        infile = Path(tmp) / "input.jsonl"
        entry = {
            "title": "WBS-001: Dry run test",
            "priority": "P2",
            "context": {"wbs_id": "WBS-001"},
        }
        infile.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run", str(infile)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        test("--dry-run exits 0", result.returncode == 0, result.stderr[:200])
        test(
            "--dry-run output references title",
            "WBS-001: Dry run test" in result.stdout,
            f"stdout={result.stdout[:300]}",
        )
        test(
            "--dry-run output does not include 'context' in payload line",
            '"context"' not in result.stdout,
            f"stdout={result.stdout[:300]}",
        )
        test(
            "--dry-run output does not include 'priority' in payload line",
            '"priority"' not in result.stdout,
            f"stdout={result.stdout[:300]}",
        )


def test_no_args_exits_nonzero() -> None:
    """Running with no arguments exits non-zero and prints help."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    test("no-args exits non-zero (prints help)", result.returncode != 0)


def test_py_compile_clean() -> None:
    """wbs-issue-gen.py has no syntax errors."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    test(
        "wbs-issue-gen.py passes py_compile",
        result.returncode == 0,
        result.stderr[:200],
    )


def main() -> int:
    print("\n-- wbs-issue-gen.py functional tests (issue #425) ---------------------")
    test_script_file_exists()
    test_py_compile_clean()
    test_schema_output()
    test_generate_jsonl_stdout()
    test_generate_jsonl_to_file()
    test_validate_valid_jsonl()
    test_validate_rejects_missing_title()
    test_validate_rejects_invalid_priority()
    test_validate_rejects_unknown_fields()
    test_validate_rejects_invalid_json()
    test_context_not_in_github_payload()
    test_dry_run_output()
    test_no_args_exits_nonzero()

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
