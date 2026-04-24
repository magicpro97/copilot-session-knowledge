#!/usr/bin/env python3
"""Validate a SKILL.md file against the Agent Skills open standard.

Spec: https://agentskills.io/specification
Repo: https://github.com/agentskills/agentskills

Usage:
    python3 validate-skill.py path/to/SKILL.md
    python3 validate-skill.py path/to/skill-dir/
"""

import sys
import re
from pathlib import Path
import os
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# --- Standards thresholds ---
MAX_LINES = 500
MIN_DESCRIPTION_WORDS = 10
MAX_HEAVY_HANDED = 5  # MUST/ALWAYS/NEVER without reasoning

def validate(path: Path) -> tuple[list[str], list[str]]:
    """Validate a SKILL.md file. Returns (errors, warnings)."""
    errors = []
    warnings = []

    if path.is_dir():
        path = path / "SKILL.md"

    if not path.exists():
        return [f"File not found: {path}"], []

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    # --- 1. YAML frontmatter ---
    if not content.startswith("---"):
        errors.append("Missing YAML frontmatter (file must start with ---)")
    else:
        fm_end = content.find("---", 3)
        if fm_end == -1:
            errors.append("Malformed YAML frontmatter (no closing ---)")
        else:
            fm = content[3:fm_end]
            if "name:" not in fm:
                errors.append("Frontmatter missing 'name' field")
            else:
                # Validate name format per Agent Skills spec:
                # 1–64 chars, a-z/0-9/hyphens, no leading/trailing/consecutive hyphens,
                # must match parent directory name.
                # Use [ \t]* (horizontal whitespace only) so a bare `name:` with no value
                # on the same line does NOT match the next line's content (e.g. description:).
                name_match = re.search(r"^name:[ \t]*['\"]?([^\s'\"#\n]+)['\"]?", fm, re.MULTILINE)
                if name_match:
                    skill_name = name_match.group(1).strip()
                    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*[a-z0-9]|[a-z0-9]", skill_name):
                        errors.append(
                            f"'name' value '{skill_name}' is invalid: must be 1–64 chars, "
                            "lowercase letters/digits/hyphens only, no leading or trailing hyphens."
                        )
                    elif "--" in skill_name:
                        errors.append(
                            f"'name' value '{skill_name}' contains consecutive hyphens (--), "
                            "which is not allowed by the Agent Skills spec."
                        )
                    elif len(skill_name) > 64:
                        errors.append(
                            f"'name' value '{skill_name}' is {len(skill_name)} chars; "
                            "max is 64 per the Agent Skills spec."
                        )
                    else:
                        # Check directory name match (only when validating a file inside a dir)
                        dir_name = path.parent.name
                        if dir_name and dir_name != "." and dir_name != skill_name:
                            warnings.append(
                                f"'name' field '{skill_name}' does not match parent directory "
                                f"'{dir_name}' — the Agent Skills spec requires them to match."
                            )
                else:
                    errors.append(
                        "'name' field has no value — it must not be empty "
                        "(e.g. `name: my-skill` not just `name:`)."
                    )
            if "description:" not in fm:
                errors.append("Frontmatter missing 'description' field")
            else:
                # Extract description text — handle YAML block scalars (>-, >+, >, |, |-…)
                # as well as quoted/unquoted single-line values.
                # Use [ \t]* (horizontal whitespace only) so a bare `description:` with no
                # value on the same line does NOT cross the newline and capture the next
                # YAML key (e.g. `name:`) as the description text.
                desc_match = re.search(r"description:[ \t]*[>|][+\-]?[ \t]*\n((?:[ \t]+.*\n)*)", fm)
                if not desc_match:
                    desc_match = re.search(r'description:[ \t]*["\']?(.+)', fm)
                if desc_match:
                    desc_text = desc_match.group(1).strip()
                    if not desc_text:
                        errors.append(
                            "'description' field has no value — it must not be empty "
                            "(e.g. `description: Use when ...` not just `description:`)."
                        )
                    else:
                        word_count = len(desc_text.split())
                        if word_count < MIN_DESCRIPTION_WORDS:
                            warnings.append(
                                f"Description only {word_count} words — aim for {MIN_DESCRIPTION_WORDS}+ "
                                f"with trigger phrases (skills under-activate without them)"
                            )
                        # Check for trigger words
                        trigger_patterns = ["use when", "trigger", "activat", "invoke", "keyword"]
                        has_triggers = any(p in desc_text.lower() for p in trigger_patterns)
                        if not has_triggers:
                            warnings.append(
                                "Description lacks trigger phrases — add 'Use when...' or keywords "
                                "to improve activation reliability"
                            )
                else:
                    errors.append(
                        "'description' field has no value — it must not be empty "
                        "(e.g. `description: Use when ...` not just `description:`)."
                    )

    # --- 2. Line count ---
    line_count = len(lines)
    if line_count > MAX_LINES:
        errors.append(
            f"File is {line_count} lines (max {MAX_LINES}). "
            f"Move detail into references/ files."
        )
    elif line_count > MAX_LINES * 0.8:
        warnings.append(
            f"File is {line_count}/{MAX_LINES} lines — approaching limit. "
            f"Consider moving detail to references/."
        )

    # --- 3. Examples ---
    example_count = content.count("<example>")
    if example_count == 0:
        errors.append(
            "No <example> blocks found. Include 1-3 realistic examples "
            "wrapped in <example> tags."
        )

    # Check for matching closing tags
    close_count = content.count("</example>")
    if example_count != close_count:
        errors.append(
            f"Mismatched example tags: {example_count} opening, {close_count} closing"
        )

    # --- 4. Required sections ---
    has_title = bool(re.search(r"^# .+", content, re.MULTILINE))
    has_when = bool(re.search(r"##.*(?:when|trigger|activat)", content, re.IGNORECASE | re.MULTILINE))
    has_workflow = bool(re.search(r"##.*(?:workflow|process|steps|how|phase|usage)", content, re.IGNORECASE | re.MULTILINE))

    if not has_title:
        errors.append("Missing title (# heading)")
    if not has_when:
        warnings.append("No 'When to use' section found — helps models decide when to activate")
    if not has_workflow:
        warnings.append("No workflow/process section found — the core content of any skill")

    # --- 5. Writing style ---
    heavy_patterns = []
    for i, line in enumerate(lines, 1):
        # Skip code blocks
        if line.strip().startswith("```") or line.strip().startswith("|"):
            continue
        # Find ALL-CAPS MUST/ALWAYS/NEVER not in a reasoning context
        matches = re.findall(r"\b(MUST|ALWAYS|NEVER|REQUIRED|MANDATORY|FORBIDDEN)\b", line)
        for m in matches:
            heavy_patterns.append((i, m, line.strip()[:80]))

    if len(heavy_patterns) > MAX_HEAVY_HANDED:
        warnings.append(
            f"Found {len(heavy_patterns)} heavy-handed directives "
            f"(MUST/ALWAYS/NEVER/etc.) — consider explaining reasoning instead. "
            f"First at line {heavy_patterns[0][0]}: '{heavy_patterns[0][2]}'"
        )

    # --- 6. Dangling references/ links ---
    # Match relative `references/<path>` links (not full paths like ~/.../references/).
    # Supports nested subdirectories (e.g. references/subdir/foo.md).
    # Non-relative patterns (shared/references/...) are excluded by the negative
    # lookbehind which rejects any `/` or word character immediately before `references/`.
    # Reports as WARNING: new check should not retroactively fail existing skills.
    skill_dir = path.parent
    # Allow `/` inside the capture so that nested paths like subdir/foo.md are captured.
    raw_mentions = re.findall(
        r"(?<![/\w])references/((?:[^\s`)\]\"]+/)*[^\s`)\]\"]+\.[a-zA-Z0-9]+)",
        content,
    )
    # Deduplicate: warn once per distinct referenced path, not once per occurrence.
    for ref_name in sorted(set(raw_mentions)):
        # Reject paths containing `..` traversal segments so that a crafted reference
        # like `references/../SKILL.md` cannot escape the references/ directory.
        # Path().parts splits on separators and preserves `..` as a literal component
        # while normalising away single `.` segments — making it safe to test each part.
        path_parts = Path(ref_name).parts  # `..` is preserved; `.` is normalised away
        if any(p == ".." for p in path_parts):
            warnings.append(
                f"Suspicious reference path skipped: `references/{ref_name}` contains "
                f"`..` — references must stay inside the skill's references/ directory."
            )
            continue
        ref_path = skill_dir / "references" / ref_name
        if not ref_path.exists():
            warnings.append(
                f"Dangling reference: `references/{ref_name}` is mentioned but "
                f"the file does not exist in the skill's references/ directory. "
                f"Create the file or remove the link (setup-project.py won't deploy it)."
            )

    return errors, warnings


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 validate-skill.py <path-to-SKILL.md>")
        print("       python3 validate-skill.py <path-to-skill-dir/>")
        sys.exit(1)

    path = Path(sys.argv[1])
    errors, warnings = validate(path)

    # --- Output ---
    if path.is_dir():
        display_path = path / "SKILL.md"
    else:
        display_path = path

    line_count = 0
    if display_path.exists():
        line_count = len(display_path.read_text(encoding="utf-8", errors="replace").splitlines())

    print(f"\n{'='*60}")
    print(f"  Skill Validation: {display_path.name}")
    print(f"  Path: {display_path}")
    print(f"  Lines: {line_count}")
    print(f"{'='*60}\n")

    if errors:
        print(f"❌ ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  • {e}")
        print()

    if warnings:
        print(f"⚠️  WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  • {w}")
        print()

    if not errors and not warnings:
        print("✅ All checks passed!\n")
    elif not errors:
        print("✅ No errors (warnings above are suggestions)\n")
    else:
        print("❌ FAIL — fix errors above before using this skill.\n")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
