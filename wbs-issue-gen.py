#!/usr/bin/env python3
"""wbs-issue-gen.py — Validate and/or generate WBS issue JSONL payloads.

Usage:
    # Validate an existing JSONL file (dry-run, no network)
    python wbs-issue-gen.py --validate wbs-issues.jsonl

    # Generate example JSONL for N issues (for testing)
    python wbs-issue-gen.py --generate 5 --output wbs-issues.jsonl

    # Dry-run: print planned REST payloads (excludes context/priority — never sent to GitHub)
    python wbs-issue-gen.py --dry-run wbs-issues.jsonl

    # Print schema help
    python wbs-issue-gen.py --schema
"""

import argparse
import json
import os
import sys
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REQUIRED_FIELDS = {"title"}
OPTIONAL_FIELDS = {"body", "labels", "milestone", "assignees", "priority", "context"}
VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
ALL_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

# Fields included in the GitHub REST API issue-creation payload.
# 'context' and 'priority' are intentionally excluded: context is operator metadata only;
# priority is set via Project v2 GraphQL in a separate step.
_GITHUB_ISSUE_FIELDS = {"title", "body", "labels", "milestone", "assignees"}


def _github_payload(entry: dict) -> dict:
    """Return only the fields that belong in a GitHub issue creation REST payload."""
    return {k: entry[k] for k in _GITHUB_ISSUE_FIELDS if k in entry}


def _validate_entry(entry: dict, line_num: int) -> list[str]:
    errors = []
    if not isinstance(entry, dict):
        return [f"line {line_num}: entry must be a JSON object"]
    missing = REQUIRED_FIELDS - entry.keys()
    if missing:
        errors.append(f"line {line_num}: missing required fields: {sorted(missing)}")
    if "title" in entry and not isinstance(entry["title"], str):
        errors.append(f"line {line_num}: 'title' must be a string")
    if "title" in entry and not entry["title"].strip():
        errors.append(f"line {line_num}: 'title' must not be empty")
    if "labels" in entry and not isinstance(entry["labels"], list):
        errors.append(f"line {line_num}: 'labels' must be an array")
    if "assignees" in entry and not isinstance(entry["assignees"], list):
        errors.append(f"line {line_num}: 'assignees' must be an array")
    if "priority" in entry:
        p = str(entry["priority"]).upper()
        if p not in VALID_PRIORITIES:
            errors.append(
                f"line {line_num}: 'priority' must be one of {sorted(VALID_PRIORITIES)}, got {entry['priority']!r}"
            )
    unknown = set(entry.keys()) - ALL_FIELDS
    if unknown:
        errors.append(f"line {line_num}: unknown fields (allowed are {sorted(ALL_FIELDS)}): {sorted(unknown)}")
    return errors


def validate(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    all_errors = []
    valid = 0
    for i, raw in enumerate(text.splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as exc:
            all_errors.append(f"line {i}: JSON parse error — {exc}")
            continue
        errs = _validate_entry(entry, i)
        if errs:
            all_errors.extend(errs)
        else:
            valid += 1
    if all_errors:
        for e in all_errors:
            print(f"  ERROR  {e}", file=sys.stderr)
        print(
            f"\nValidation FAILED: {len(all_errors)} error(s), {valid} valid entries",
            file=sys.stderr,
        )
        return 1
    print(f"Validation OK: {valid} valid entries")
    return 0


def generate(n: int, output: Path | None) -> None:
    lines = []
    priorities = ["P0", "P1", "P2", "P3"]
    for i in range(1, n + 1):
        entry = {
            "title": f"WBS-{i:03d}: Example issue {i}",
            "body": (
                f"## Goal\n\nImplement WBS item {i}.\n\n## Acceptance Criteria\n\n- [ ] Tests pass\n- [ ] Docs updated"
            ),
            "labels": ["wbs", priorities[i % 4].lower()],
            "priority": priorities[i % 4],
            "context": {"wbs_id": f"WBS-{i:03d}", "generated": True},
        }
        lines.append(json.dumps(entry, ensure_ascii=False))
    payload = "\n".join(lines) + "\n"
    if output:
        output.write_text(payload, encoding="utf-8")
        print(f"Generated {n} entries → {output}")
    else:
        sys.stdout.write(payload)


def dry_run(path: Path) -> int:
    """Print what REST payloads would be sent — no network, excludes context and priority."""
    text = path.read_text(encoding="utf-8")
    count = 0
    for i, raw in enumerate(text.splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"  ERROR  line {i}: JSON parse error — {exc}", file=sys.stderr)
            return 1
        payload = _github_payload(entry)
        print(f"[dry-run] issue {count + 1}: POST /repos/$REPO/issues  title={payload.get('title')!r}")
        print(f"          payload: {json.dumps(payload)}")
        count += 1
    print(f"\nDry-run complete: {count} issue(s) would be created.")
    return 0


SCHEMA_HELP = """
WBS Issue JSONL Schema
======================
Each line = one JSON object.

REQUIRED:
  title        string  Issue title (non-empty)

OPTIONAL:
  body         string  GitHub Flavored Markdown body
  labels       array   Label name strings (e.g. ["wbs","p1"])
  milestone    string  Milestone title
  assignees    array   GitHub usernames
  priority     string  P0 | P1 | P2 | P3  (Project v2 Priority field — set via GraphQL, not REST)
  context      object  Operator metadata (wbs_id, wave, surface, …) — NOT sent to GitHub
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="WBS issue JSONL validator / generator")
    parser.add_argument("--validate", metavar="FILE", help="Validate a JSONL file (offline)")
    parser.add_argument("--generate", metavar="N", type=int, help="Generate N example entries")
    parser.add_argument("--output", metavar="FILE", help="Output path for --generate")
    parser.add_argument("--dry-run", metavar="FILE", help="Print planned REST payloads (no network)")
    parser.add_argument("--schema", action="store_true", help="Print schema reference")
    args = parser.parse_args()

    if args.schema:
        print(SCHEMA_HELP)
        return 0
    if args.validate:
        return validate(Path(args.validate))
    if args.generate is not None:
        generate(args.generate, Path(args.output) if args.output else None)
        return 0
    if args.dry_run:
        return dry_run(Path(args.dry_run))
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
