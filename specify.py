#!/usr/bin/env python3
"""
specify.py - Generate structured spec, plan, and task artifacts.

Usage:
    python specify.py "Hosted shell bootstrap"
    python specify.py "Hosted shell bootstrap" --slug hosted-shell-bootstrap --json
    python specify.py plan hosted-shell-bootstrap --json
    python specify.py tasks hosted-shell-bootstrap --json
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

DEFAULT_SPECS_DIR = Path("specs")
ARTIFACT_FILENAMES = ("spec.md", "plan.md", "tasks.md")
STORY_BLUEPRINTS = [
    {
        "story_id": "US1",
        "priority": "P1",
        "headline": "Primary user journey",
        "story": "As a primary user, I want {topic} to support the core workflow so the main outcome is unblocked.",
        "given": "the repository context and required inputs for {topic} are available",
        "when": "the primary workflow for {topic} is executed end-to-end",
        "then": "the core outcome completes with explicit, testable behavior",
    },
    {
        "story_id": "US2",
        "priority": "P2",
        "headline": "Supporting operator workflow",
        "story": "As an operator, I want {topic} to expose the supporting workflow so coordination stays visible.",
        "given": "downstream coordination or operator review is required for {topic}",
        "when": "the supporting workflow for {topic} is prepared or executed",
        "then": "the operator-visible checkpoints remain clear and actionable",
    },
    {
        "story_id": "US3",
        "priority": "P3",
        "headline": "Validation and fallback coverage",
        "story": "As a maintainer, I want {topic} to document validation and fallback behavior so regressions are easier to catch.",
        "given": "edge cases, invalid input, or verification gates exist around {topic}",
        "when": "the workflow is validated or recovery steps are needed",
        "then": "the expected checks and fallback handling stay explicit",
    },
]
REQUIREMENT_BLUEPRINTS = [
    "FR-001 The system MUST capture the core scope for {topic} in a structured, reviewable specification.",
    "FR-002 The system MUST preserve downstream planning context for {topic} so operators can trace implementation work.",
    "FR-003 The system MUST keep validation and success criteria for {topic} explicit and testable.",
]
SUCCESS_CRITERIA_BLUEPRINTS = [
    "SC-001 The primary flow for {topic} can be reasoned about from the generated artifacts without extra scaffolding.",
    "SC-002 The generated artifacts preserve traceability across user stories, functional requirements, and validation work.",
    "SC-003 Tentacles and downstream execution can reference the same artifact bundle for {topic}.",
]
TITLE_RE = re.compile(r"^# Specification:\s*(?P<title>.+)$", re.MULTILINE)
SPEC_ID_RE = re.compile(r"^Spec ID:\s*(?P<slug>[a-z0-9-]+)\s*$", re.MULTILINE)
STORY_HEADER_RE = re.compile(r"^### (?P<story_id>US\d+) — (?P<priority>P\d) — (?P<headline>.+)$")
STORY_LINE_RE = re.compile(r"^- Story:\s*(?P<value>.+)$", re.MULTILINE)
GIVEN_LINE_RE = re.compile(r"^- Given\s+(?P<value>.+)$", re.MULTILINE)
WHEN_LINE_RE = re.compile(r"^- When\s+(?P<value>.+)$", re.MULTILINE)
THEN_LINE_RE = re.compile(r"^- Then\s+(?P<value>.+)$", re.MULTILINE)
REQUIREMENT_RE = re.compile(r"^- (?P<id>FR-\d{3}) (?P<text>.+)$", re.MULTILINE)
SUCCESS_RE = re.compile(r"^- (?P<id>SC-\d{3}) (?P<text>.+)$", re.MULTILINE)


class PhaseUsageError(Exception):
    """Raised for invalid CLI usage without exiting the interpreter."""


class _PhaseArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - exercised via main()
        raise PhaseUsageError(message)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug or "spec"


def _relative_posix(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _bundle_dir(repo_root: Path, slug: str) -> Path:
    return repo_root / DEFAULT_SPECS_DIR / slug


def _artifact_paths(repo_root: Path, slug: str) -> dict[str, Path]:
    bundle = _bundle_dir(repo_root, slug)
    return {
        "bundle": bundle,
        "spec": bundle / "spec.md",
        "plan": bundle / "plan.md",
        "tasks": bundle / "tasks.md",
    }


def _specify_document(title: str, slug: str) -> str:
    lines = [
        f"# Specification: {title}",
        "",
        f"Spec ID: {slug}",
        f"Created: {_now_iso()}",
        "",
        "## User Stories",
        "",
    ]
    for story in STORY_BLUEPRINTS:
        lines.extend(
            [
                f"### {story['story_id']} — {story['priority']} — {story['headline']}",
                f"- Story: {story['story'].format(topic=title)}",
                f"- Given {story['given'].format(topic=title)}",
                f"- When {story['when'].format(topic=title)}",
                f"- Then {story['then'].format(topic=title)}",
                "",
            ]
        )
    lines.extend(["## Functional Requirements", ""])
    for requirement in REQUIREMENT_BLUEPRINTS:
        lines.append(f"- {requirement.format(topic=title)}")
    lines.extend(["", "## Success Criteria", ""])
    for criterion in SUCCESS_CRITERIA_BLUEPRINTS:
        lines.append(f"- {criterion.format(topic=title)}")
    lines.append("")
    return "\n".join(lines)


def _load_spec_data(spec_path: Path) -> dict:
    text = spec_path.read_text(encoding="utf-8")
    title_match = TITLE_RE.search(text)
    slug_match = SPEC_ID_RE.search(text)
    stories = []
    story_section = ""
    if "## User Stories" in text and "## Functional Requirements" in text:
        story_section = text.split("## User Stories", 1)[1].split("## Functional Requirements", 1)[0]
    for raw_block in re.split(r"^### ", story_section, flags=re.MULTILINE):
        block = raw_block.strip()
        if not block:
            continue
        lines = block.splitlines()
        header_match = STORY_HEADER_RE.match(f"### {lines[0].strip()}")
        if not header_match:
            continue
        body = "\n".join(lines[1:])
        story = STORY_LINE_RE.search(body)
        given = GIVEN_LINE_RE.search(body)
        when = WHEN_LINE_RE.search(body)
        then = THEN_LINE_RE.search(body)
        stories.append(
            {
                "story_id": header_match.group("story_id"),
                "priority": header_match.group("priority"),
                "headline": header_match.group("headline").strip(),
                "story": story.group("value").strip() if story else "",
                "given": given.group("value").strip() if given else "",
                "when": when.group("value").strip() if when else "",
                "then": then.group("value").strip() if then else "",
            }
        )
    requirements = [{"id": m.group("id"), "text": m.group("text").strip()} for m in REQUIREMENT_RE.finditer(text)]
    criteria = [{"id": m.group("id"), "text": m.group("text").strip()} for m in SUCCESS_RE.finditer(text)]
    if not title_match or not slug_match:
        raise ValueError(f"specify: malformed spec at {spec_path}")
    if len(stories) < 3 or len(requirements) < 3 or len(criteria) < 3:
        raise ValueError(f"specify: incomplete structured spec at {spec_path}")
    if len(stories) != len(requirements) or len(stories) != len(criteria):
        raise ValueError(f"specify: story / requirement / success-criteria counts must match in {spec_path}")
    repo_root = _find_git_root(spec_path.parent)
    return {
        "title": title_match.group("title").strip(),
        "slug": slug_match.group("slug").strip(),
        "stories": stories,
        "requirements": requirements,
        "criteria": criteria,
        "spec_path": spec_path.resolve(),
        "spec_rel": _relative_posix(repo_root, spec_path),
    }


def _resolve_bundle(repo_root: Path, spec_ref: str | None) -> Path:
    specs_root = repo_root / DEFAULT_SPECS_DIR
    if spec_ref:
        candidate = Path(spec_ref)
        if not candidate.is_absolute():
            repo_candidate = (repo_root / candidate).resolve()
            slug_candidate = (specs_root / spec_ref).resolve()
            if repo_candidate.exists():
                candidate = repo_candidate
            else:
                candidate = slug_candidate
        if candidate.is_file():
            if candidate.name != "spec.md":
                raise ValueError("specify: expected a spec.md file or bundle directory")
            bundle = candidate.parent
        else:
            bundle = candidate
        spec_path = bundle / "spec.md"
        if not spec_path.exists():
            raise ValueError(f"specify: spec.md not found for '{spec_ref}'")
        return bundle.resolve()

    bundles = sorted(path.parent for path in specs_root.glob("*/spec.md"))
    if not bundles:
        raise ValueError("specify: no spec bundles found under specs/ (run `sk specify` first)")
    if len(bundles) > 1:
        raise ValueError("specify: multiple spec bundles found; pass a slug or path")
    return bundles[0].resolve()


def _render_plan(spec: dict) -> str:
    lines = [
        f"# Plan: {spec['title']}",
        "",
        f"Spec ID: {spec['slug']}",
        f"Source Spec: `{spec['spec_rel']}`",
        f"Created: {_now_iso()}",
        "",
        "## Architecture Decisions",
        f"- AD-001 Keep the artifact chain in `specs/{spec['slug']}/` so downstream tentacles can reference one stable bundle.",
        "- AD-002 Preserve user story, FR, and SC identifiers verbatim across every downstream planning artifact.",
        "- AD-003 Surface the generated artifact bundle in tentacle context so implementation and handoff work can trace back to the source spec.",
        "",
        "## Implementation Phases",
        "1. Clarify any remaining gaps in the structured user stories and scenarios.",
        "2. Build the change around the functional requirements with the same identifiers.",
        "3. Validate the success criteria and update tentacle references or handoffs.",
        "",
        "## Story Traceability",
        "",
    ]
    for index, story in enumerate(spec["stories"], 1):
        requirement = spec["requirements"][index - 1]
        criterion = spec["criteria"][index - 1]
        lines.extend(
            [
                f"### {story['story_id']} — {story['priority']} — {story['headline']}",
                f"- Story: {story['story']}",
                f"- Requirements: {requirement['id']}",
                f"- Success Criteria: {criterion['id']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Risks / Open Questions",
            "",
            "- Confirm repo-specific constraints, interfaces, and verification scope before implementation begins.",
            "- Keep any new tasks or tentacles aligned with the FR / SC identifiers above.",
            "",
        ]
    )
    return "\n".join(lines)


def _render_tasks(spec: dict, plan_exists: bool) -> str:
    lines = [
        f"# Tasks: {spec['title']}",
        "",
        f"Source Spec: `{spec['spec_rel']}`",
        (
            f"Source Plan: `specs/{spec['slug']}/plan.md`"
            if plan_exists
            else f"Source Plan: `specs/{spec['slug']}/plan.md` (generate it with `sk plan {spec['slug']}` when architecture details are ready)"
        ),
        f"Created: {_now_iso()}",
        "",
        "## Task Checklist",
        "",
    ]
    task_number = 1
    for index, story in enumerate(spec["stories"], 1):
        requirement = spec["requirements"][index - 1]
        criterion = spec["criteria"][index - 1]
        lines.append(
            f"- [ ] T{task_number:03d} [{story['story_id']}] Review the {story['priority']} scope for {story['headline'].lower()} and confirm the inputs for {requirement['id']}."
        )
        task_number += 1
        lines.append(
            f"- [ ] T{task_number:03d} [P] [{story['story_id']}] Implement the work needed for {requirement['id']} and validate it against {criterion['id']}."
        )
        task_number += 1
    lines.append(
        f"- [ ] T{task_number:03d} [P] [US1] Run focused verification for the generated artifact chain and keep any tentacle references aligned with `specs/{spec['slug']}/`."
    )
    lines.append("")
    return "\n".join(lines)


def _payload(command: str, repo_root: Path, spec: dict, paths: dict[str, Path]) -> dict:
    return {
        "command": command,
        "title": spec["title"],
        "slug": spec["slug"],
        "bundle_path": str(paths["bundle"]),
        "spec_path": str(paths["spec"]),
        "plan_path": str(paths["plan"]),
        "tasks_path": str(paths["tasks"]),
        "artifacts": {
            "spec": _relative_posix(repo_root, paths["spec"]),
            "plan": _relative_posix(repo_root, paths["plan"]),
            "tasks": _relative_posix(repo_root, paths["tasks"]),
        },
    }


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _build_parser(command: str) -> _PhaseArgumentParser:
    description = {
        "specify": "Generate a structured spec.md bundle.",
        "plan": "Generate plan.md from a structured spec bundle.",
        "tasks": "Generate tasks.md from a structured spec bundle.",
    }[command]
    parser = _PhaseArgumentParser(description=description)
    if command == "specify":
        parser.add_argument("topic", help="Human-readable title for the spec bundle.")
        parser.add_argument("--slug", help="Optional slug for specs/<slug>/ (defaults to the topic slug).")
    else:
        parser.add_argument("spec", nargs="?", help="Spec slug, bundle directory, or spec.md path.")
    parser.add_argument("--repo", help="Repo root to operate on (defaults to current git root).")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing artifact.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(argv if argv is not None else sys.argv[1:])
    command = "specify"
    if raw_args and raw_args[0] in {"specify", "plan", "tasks"}:
        command = raw_args.pop(0)
    parser = _build_parser(command)
    try:
        args = parser.parse_args(raw_args)
        repo_root = _resolve_repo_root(getattr(args, "repo", None))

        if command == "specify":
            slug = _slugify(args.slug or args.topic)
            paths = _artifact_paths(repo_root, slug)
            if paths["spec"].exists() and not args.force:
                print(f"specify: spec already exists at {paths['spec']} (use --force to overwrite)", file=sys.stderr)
                return 1
            paths["bundle"].mkdir(parents=True, exist_ok=True)
            _atomic_write_text(paths["spec"], _specify_document(args.topic.strip(), slug))
            spec = _load_spec_data(paths["spec"])
        else:
            bundle = _resolve_bundle(repo_root, getattr(args, "spec", None))
            spec = _load_spec_data(bundle / "spec.md")
            paths = _artifact_paths(repo_root, spec["slug"])
            target = paths[command]
            if target.exists() and not args.force:
                print(f"{command}: artifact already exists at {target} (use --force to overwrite)", file=sys.stderr)
                return 1
            content = _render_plan(spec) if command == "plan" else _render_tasks(spec, paths["plan"].exists())
            _atomic_write_text(target, content)

        result = _payload(command, repo_root, spec, paths)
        if args.json:
            _print_json(result)
        else:
            artifact = {"specify": paths["spec"], "plan": paths["plan"], "tasks": paths["tasks"]}[command]
            print(f"{command}: wrote {artifact}")
        return 0
    except PhaseUsageError as exc:
        print(f"{command}: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
