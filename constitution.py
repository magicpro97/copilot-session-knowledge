#!/usr/bin/env python3
"""
constitution.py - Manage project-local constitution governance.

Usage:
    python constitution.py init
    python constitution.py check
    python constitution.py amend --summary "Add release approval gate" --bump minor
    python constitution.py amend --summary "Clarify worktree policy" --principle "Use isolated worktrees. [rule:no-destructive-git]"
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_CONSTITUTION_PATH = Path(".copilot") / "constitution.md"
SECTION_ORDER = ("Principles", "Quality Gates", "Governance", "Changelog")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
RULE_TAG_RE = re.compile(r"\[rule:(?P<rule>[a-z0-9-]+)\]", re.IGNORECASE)
KNOWN_RULE_IDS = {
    "no-destructive-git",
    "no-force-push",
}


class ConstitutionUsageError(Exception):
    """Raised for CLI usage issues without exiting the interpreter."""


class _ConstitutionArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - exercised via main()
        raise ConstitutionUsageError(message)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content.encode(encoding))
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _find_git_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return current


def _resolve_repo_root(repo: str | None) -> Path:
    if repo:
        return Path(repo).expanduser().resolve()
    return _find_git_root()


def _constitution_path(repo_root: Path) -> Path:
    return repo_root / DEFAULT_CONSTITUTION_PATH


def _empty_document() -> dict:
    return {
        "title": "Project Constitution",
        "version": "",
        "last_amended": "",
        "sections": {section: [] for section in SECTION_ORDER},
    }


def _default_document() -> dict:
    doc = _empty_document()
    now = _now_iso()
    today = _today_iso()
    doc["version"] = "1.0.0"
    doc["last_amended"] = now
    doc["sections"]["Principles"] = [
        "**Protect History** — Never use destructive git commands on tracked work. [rule:no-destructive-git]",
        "**Push Safely** — Do not force-push shared branches. [rule:no-force-push]",
        "**Verify Before Claims** — Back correctness claims with executed validation evidence.",
    ]
    doc["sections"]["Quality Gates"] = [
        "Run the relevant focused tests before calling work complete.",
        "Keep public CLI/help surfaces aligned with the commands you introduce.",
        "Preserve fail-open or explicit-error behavior that matches existing repo patterns.",
    ]
    doc["sections"]["Governance"] = [
        "MAJOR updates change or remove a governing principle.",
        "MINOR updates add a new principle, quality gate, or enforcement rule.",
        "PATCH updates clarify wording, examples, or operator guidance without changing intent.",
    ]
    doc["sections"]["Changelog"] = [
        f"1.0.0 | {today} | Initialized constitution.",
    ]
    return doc


def _strip_rule_tags(text: str) -> str:
    cleaned = RULE_TAG_RE.sub("", str(text or ""))
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _extract_rule_tags(text: str) -> list[str]:
    return [match.group("rule").lower() for match in RULE_TAG_RE.finditer(str(text or ""))]


def _parse_document(text: str) -> dict:
    doc = _empty_document()
    current_section = None
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# ") and doc["title"] == "Project Constitution":
            doc["title"] = stripped[2:].strip() or doc["title"]
            continue
        lowered = stripped.lower()
        if lowered.startswith("version:"):
            doc["version"] = stripped.split(":", 1)[1].strip()
            continue
        if lowered.startswith("last amended:"):
            doc["last_amended"] = stripped.split(":", 1)[1].strip()
            continue
        if stripped.startswith("## "):
            section_name = stripped[3:].strip()
            current_section = section_name if section_name in doc["sections"] else None
            continue
        if current_section and stripped.startswith("- "):
            doc["sections"][current_section].append(stripped[2:].strip())
    return doc


def _render_document(doc: dict) -> str:
    lines = [f"# {doc.get('title') or 'Project Constitution'}", ""]
    lines.append(f"Version: {doc.get('version', '').strip()}")
    lines.append(f"Last Amended: {doc.get('last_amended', '').strip()}")
    lines.append("")
    for section in SECTION_ORDER:
        lines.append(f"## {section}")
        for item in doc["sections"].get(section, []):
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _load_document(path: Path) -> dict:
    return _parse_document(path.read_text(encoding="utf-8"))


def _bump_version(version: str, bump: str) -> str:
    major, minor, patch = [int(part) for part in version.split(".")]
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _validate_document(doc: dict) -> list[str]:
    violations: list[str] = []
    title = str(doc.get("title", "")).strip()
    if not title:
        violations.append("missing title")

    version = str(doc.get("version", "")).strip()
    if not version:
        violations.append("missing version")
    elif not VERSION_RE.match(version):
        violations.append("version must follow MAJOR.MINOR.PATCH")

    if not str(doc.get("last_amended", "")).strip():
        violations.append("missing Last Amended metadata")

    sections = doc.get("sections", {})
    for section in SECTION_ORDER:
        if not sections.get(section):
            violations.append(f"missing {section} section content")

    changelog = sections.get("Changelog", [])
    if changelog and version and not changelog[0].startswith(version):
        violations.append("latest changelog entry must start with the current version")

    for principle in sections.get("Principles", []):
        for rule_id in _extract_rule_tags(principle):
            if rule_id not in KNOWN_RULE_IDS:
                violations.append(f"unknown rule tag: {rule_id}")

    return violations


def _serialize_document(
    doc: dict, path: Path, *, valid: bool | None = None, violations: list[str] | None = None
) -> dict:
    principles = list(doc["sections"].get("Principles", []))
    return {
        "path": str(path),
        "title": doc.get("title", ""),
        "version": doc.get("version", ""),
        "last_amended": doc.get("last_amended", ""),
        "principles": [_strip_rule_tags(item) for item in principles],
        "quality_gates": [_strip_rule_tags(item) for item in doc["sections"].get("Quality Gates", [])],
        "governance": [_strip_rule_tags(item) for item in doc["sections"].get("Governance", [])],
        "changelog": list(doc["sections"].get("Changelog", [])),
        "rule_tags": sorted({rule for item in principles for rule in _extract_rule_tags(item)}),
        "valid": valid,
        "violations": list(violations or []),
    }


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _build_parser() -> _ConstitutionArgumentParser:
    parser = _ConstitutionArgumentParser(description="Manage project-local constitution governance.")
    subparsers = parser.add_subparsers(dest="subcommand")

    init_parser = subparsers.add_parser("init", help="Create a constitution template.")
    init_parser.add_argument("--repo", help="Repo root to operate on (defaults to current git root).")
    init_parser.add_argument("--force", action="store_true", help="Overwrite an existing constitution.")
    init_parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    check_parser = subparsers.add_parser("check", help="Validate the constitution document.")
    check_parser.add_argument("--repo", help="Repo root to operate on (defaults to current git root).")
    check_parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    amend_parser = subparsers.add_parser("amend", help="Append amendments and bump the constitution version.")
    amend_parser.add_argument("--repo", help="Repo root to operate on (defaults to current git root).")
    amend_parser.add_argument("--bump", choices=["major", "minor", "patch"], default="patch")
    amend_parser.add_argument("--summary", required=True, help="Changelog summary for this amendment.")
    amend_parser.add_argument("--principle", action="append", default=[], help="Principle bullet to append.")
    amend_parser.add_argument("--quality-gate", action="append", default=[], help="Quality gate bullet to append.")
    amend_parser.add_argument("--governance", action="append", default=[], help="Governance bullet to append.")
    amend_parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        if not args.subcommand:
            raise ConstitutionUsageError("a subcommand is required: init, check, or amend")

        repo_root = _resolve_repo_root(getattr(args, "repo", None))
        constitution_path = _constitution_path(repo_root)

        if args.subcommand == "init":
            if constitution_path.exists() and not args.force:
                print(
                    f"constitution: already exists at {constitution_path} (use --force to overwrite)",
                    file=sys.stderr,
                )
                return 1
            doc = _default_document()
            constitution_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(constitution_path, _render_document(doc))
            payload = _serialize_document(doc, constitution_path, valid=True, violations=[])
            if args.json:
                _print_json(payload)
            else:
                print(f"Initialized constitution: {constitution_path}")
                print(f"Version: {doc['version']}")
            return 0

        if not constitution_path.exists():
            payload = {
                "path": str(constitution_path),
                "valid": False,
                "violations": ["constitution file not found"],
            }
            if getattr(args, "json", False):
                _print_json(payload)
            else:
                print(f"constitution: file not found at {constitution_path}", file=sys.stderr)
            return 1

        doc = _load_document(constitution_path)

        if args.subcommand == "check":
            violations = _validate_document(doc)
            payload = _serialize_document(doc, constitution_path, valid=not violations, violations=violations)
            if args.json:
                _print_json(payload)
            else:
                print(f"Constitution: {constitution_path}")
                print(f"Version: {doc.get('version', '') or '(missing)'}")
                if violations:
                    print("Violations:")
                    for violation in violations:
                        print(f"- {violation}")
                else:
                    print("Status: valid")
            return 0 if not violations else 1

        if args.subcommand == "amend":
            violations = _validate_document(doc)
            if violations:
                if args.json:
                    _print_json(_serialize_document(doc, constitution_path, valid=False, violations=violations))
                else:
                    print("constitution: cannot amend an invalid constitution:", file=sys.stderr)
                    for violation in violations:
                        print(f"- {violation}", file=sys.stderr)
                return 1

            doc["version"] = _bump_version(doc["version"], args.bump)
            doc["last_amended"] = _now_iso()
            doc["sections"]["Principles"].extend([item.strip() for item in args.principle if item.strip()])
            doc["sections"]["Quality Gates"].extend([item.strip() for item in args.quality_gate if item.strip()])
            doc["sections"]["Governance"].extend([item.strip() for item in args.governance if item.strip()])
            doc["sections"]["Changelog"].insert(
                0,
                f"{doc['version']} | {_today_iso()} | {args.summary.strip()}",
            )
            post_mutation_violations = _validate_document(doc)
            if post_mutation_violations:
                if args.json:
                    _print_json(
                        _serialize_document(
                            doc,
                            constitution_path,
                            valid=False,
                            violations=post_mutation_violations,
                        )
                    )
                else:
                    print("constitution: amendment would make the constitution invalid:", file=sys.stderr)
                    for violation in post_mutation_violations:
                        print(f"- {violation}", file=sys.stderr)
                return 1
            _atomic_write_text(constitution_path, _render_document(doc))
            payload = _serialize_document(doc, constitution_path, valid=True, violations=[])
            if args.json:
                _print_json(payload)
            else:
                print(f"Amended constitution: {constitution_path}")
                print(f"Version: {doc['version']}")
                print(f"Summary: {args.summary.strip()}")
            return 0

        raise ConstitutionUsageError(f"unknown subcommand: {args.subcommand}")
    except ConstitutionUsageError as exc:
        print(f"constitution: {exc}", file=sys.stderr)
        print(parser.format_usage().strip(), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"constitution: filesystem error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"constitution: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
