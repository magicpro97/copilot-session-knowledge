#!/usr/bin/env python3
"""lint-skills.py — Validate .agent.md and SKILL.md files against Copilot CLI schemas.

Catches silently-ignored issues:
  - Deprecated 'infer' field (use 'user-invocable' + 'disable-model-invocation')
  - Unknown frontmatter fields
  - Invalid/cross-platform tool names
  - Missing required fields
  - Schema confusion (agent vs skill field usage)

Auto-detects CLI version and parses schemas from app.js when available.

Exit codes:
  0 = all clean
  1 = errors found (blocks commit)
  2 = warnings only (allows commit, prints warnings)

Usage:
  python3 lint-skills.py                    # scan default locations
  python3 lint-skills.py path/to/file.md    # scan specific files
  python3 lint-skills.py --all              # scan entire ~/.copilot/tools/skills/
  python3 lint-skills.py --fix              # show suggested fixes
"""

import os
import sys
import re
from pathlib import Path
from typing import NamedTuple


# ─── Auto-detect schemas from installed CLI ──────────────────────────────

def _find_latest_app_js() -> Path | None:
    """Find the latest Copilot CLI app.js file."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "copilot" / "pkg"
    else:
        # WSL or Linux — check Windows path via /mnt/c
        home = Path.home()
        # Try WSL path first
        for p in [
            Path("/mnt/c/Users") / home.name / ".copilot" / "pkg",
            home / ".copilot" / "pkg",
        ]:
            if p.exists():
                base = p
                break
        else:
            return None

    universal = base / "universal"
    if not universal.exists():
        return None

    # Find latest version directory
    versions = []
    for d in universal.iterdir():
        if d.is_dir() and (d / "app.js").exists():
            try:
                parts = [int(x) for x in d.name.split(".")]
                versions.append((parts, d))
            except ValueError:
                continue

    if not versions:
        return None

    versions.sort(key=lambda x: x[0])
    return versions[-1][1] / "app.js"


def _parse_tool_map_from_appjs(app_js: Path) -> set[str] | None:
    """Extract valid tool category names from T_n in app.js."""
    try:
        content = app_js.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    # Find T_n={...} or equivalent variable holding tool categories
    m = re.search(r'\w+=\{execute:\["bash","powershell"\]', content)
    if not m:
        return None

    start = m.start()
    chunk = content[start:]
    eq_pos = chunk.index("=")
    obj_str = chunk[eq_pos + 1:]

    # Parse balanced braces
    depth = 0
    end = 0
    for i, c in enumerate(obj_str):
        if c == "{":
            depth += 1
        if c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    obj_text = obj_str[:end]

    # Extract all keys (both quoted and unquoted)
    keys = set()
    for km in re.finditer(r'(?:^|,|\{)\s*"?([a-zA-Z_][a-zA-Z0-9_-]*)"?\s*:', obj_text):
        keys.add(km.group(1))

    return keys if keys else None


def _parse_agent_fields_from_appjs(app_js: Path) -> set[str] | None:
    """Extract valid agent frontmatter fields from Zod schema in app.js."""
    try:
        content = app_js.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    # Agent schema has: description, tools, mcp-servers, infer, disable-model-invocation, etc.
    m = re.search(r'description:\w+\.string\(\),tools:\w+,"mcp-servers"', content)
    if not m:
        return None

    start = max(0, m.start() - 200)
    chunk = content[start : m.start() + 500]

    fields = set()
    for fm in re.finditer(r'"([a-z][a-z-]*)"', chunk):
        fields.add(fm.group(1))
    # Also catch unquoted keys before Zod calls or variable references
    for fm in re.finditer(r'(?:^|,|\{)\s*(\w+)\s*:', chunk):
        key = fm.group(1)
        if key and not key[0].isupper() and key not in ("ne", "De"):
            fields.add(key)

    return fields if fields else None


def _detect_cli_version(app_js: Path) -> str | None:
    """Detect CLI version from app.js path."""
    for part in app_js.parts:
        if re.match(r"\d+\.\d+\.\d+", part):
            return part
    return None


# ─── Schemas ─────────────────────────────────────────────────────────────

# Fallback hardcoded schemas (from CLI v1.0.25, updated as needed)
_FALLBACK_AGENT_FIELDS = {
    "name", "description", "tools", "mcp-servers",
    "disable-model-invocation", "user-invocable",
    "model", "github", "skills", "handoffs",
    "infer",  # deprecated but still parsed
}

_FALLBACK_TOOL_CATEGORIES = {
    "execute", "shell", "bash", "powershell",
    "read", "NotebookRead", "edit", "MultiEdit", "Write", "NotebookEdit",
    "search", "Grep", "Glob", "grep",
    "custom-agent", "agent", "task",
    "runCommands", "runCommands-runInTerminal", "runInTerminal",
    "edit-editFiles", "editFiles",
    "create-createFile", "createFile", "createDirectory",
    "search-codebase", "codebase",
    "search-fileSearch", "fileSearch",
    "search-textSearch", "textSearch",
    "search-readFile", "readFile",
    # Additional known tools not in T_n but valid:
    "create", "skill", "view", "glob",
    "web_search", "web_fetch", "ask_user", "sql", "lsp",
}

# Try auto-detect from installed CLI
_APP_JS = _find_latest_app_js()
_CLI_VERSION = _detect_cli_version(_APP_JS) if _APP_JS else None

_auto_tools = _parse_tool_map_from_appjs(_APP_JS) if _APP_JS else None
_auto_agent_fields = _parse_agent_fields_from_appjs(_APP_JS) if _APP_JS else None

if _auto_tools:
    VALID_CLI_TOOLS = _auto_tools | {"create", "skill", "view", "glob",
                                      "web_search", "web_fetch", "ask_user",
                                      "sql", "lsp"}
else:
    VALID_CLI_TOOLS = _FALLBACK_TOOL_CATEGORIES

if _auto_agent_fields:
    AGENT_VALID_FIELDS = _auto_agent_fields | {"name", "handoffs", "infer"}
else:
    AGENT_VALID_FIELDS = _FALLBACK_AGENT_FIELDS

SKILL_VALID_FIELDS = {
    "name", "description", "allowed-tools",
    "user-invocable", "disable-model-invocation",
    # Cross-platform fields (Claude Code / Kiro) — not used by Copilot CLI
    # but harmless and intentional for multi-platform compatibility:
    "aliases", "context", "model", "skills", "hooks",
    "license", "metadata", "version",
}

# VS Code tool names (NOT valid in CLI)
VSCODE_TOOL_PATTERNS = [
    r"^search/",      # search/codebase, search/usages
    r"^web/",          # web/fetch
    r"^read/",         # read/problems, read/readFile
    r"^edit/",         # edit/editFiles
    r"^execute/",      # execute/runTests, execute/runInTerminal
]

# Claude Code tool names (NOT valid in Copilot CLI)
CLAUDE_TOOLS = {
    "Bash", "Read", "Write", "Edit", "Grep", "Glob", "Task",
    "WebFetch", "WebSearch", "MultiTool", "NotebookEdit",
    "TodoRead", "TodoWrite", "AskUserQuestion",
}


class Issue(NamedTuple):
    level: str      # "error" or "warning"
    file: str
    line: int
    code: str       # e.g. "SK-001"
    message: str
    fix: str        # suggested fix or ""


def parse_frontmatter(content: str) -> tuple[dict, int]:
    """Parse YAML frontmatter from markdown content.
    Returns (fields_dict, end_line_number).
    """
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, 0

    fields = {}
    end_line = 0
    in_multiline = False
    current_key = None

    for i, line in enumerate(lines[1:], start=2):
        stripped = line.strip()
        if stripped == "---":
            end_line = i
            break

        # Skip continuation lines of multiline scalar values (|, >, >-)
        if in_multiline:
            if line.startswith("  ") or line.startswith("\t"):
                continue
            in_multiline = False

        # Collect YAML block sequence items (  - value)
        if current_key and re.match(r"^\s+-\s+", line):
            item = re.sub(r"^\s+-\s+", "", line).strip().strip("'\"")
            if item:
                prev = fields[current_key]["value"]
                if prev:
                    fields[current_key]["value"] = prev + ", " + item
                else:
                    fields[current_key]["value"] = item
            continue

        # Parse key: value
        match = re.match(r"^([a-zA-Z_-]+)\s*:\s*(.*)", line)
        if match:
            key = match.group(1).strip()
            value = match.group(2).strip()
            fields[key] = {"value": value, "line": i}
            current_key = key
            if value in ("|", ">", ">-"):
                in_multiline = True
        else:
            current_key = None

    return fields, end_line


def parse_tools_list(value: str) -> list[str]:
    """Parse tools from YAML value — handles arrays and strings."""
    value = value.strip()

    # YAML array: ['tool1', 'tool2'] or [tool1, tool2]
    if value.startswith("["):
        inner = value.strip("[]")
        tools = []
        for item in inner.split(","):
            item = item.strip().strip("'\"")
            if item:
                tools.append(item)
        return tools

    # Comma-separated string: "tool1, tool2"
    if "," in value:
        return [t.strip().strip("'\"") for t in value.split(",") if t.strip()]

    # Single tool
    if value:
        return [value.strip("'\"")]

    return []


def is_vscode_tool(name: str) -> bool:
    return any(re.match(p, name) for p in VSCODE_TOOL_PATTERNS)


def is_claude_tool(name: str) -> bool:
    return name in CLAUDE_TOOLS


def lint_agent_file(filepath: Path, content: str) -> list[Issue]:
    """Lint a .agent.md file against the Copilot CLI agent schema."""
    issues = []
    fields, _ = parse_frontmatter(content)

    if not fields:
        issues.append(Issue("error", str(filepath), 1, "AG-001",
                            "Missing YAML frontmatter (---)", "Add frontmatter block"))
        return issues

    # Required fields
    if "name" not in fields:
        issues.append(Issue("error", str(filepath), 1, "AG-002",
                            "Missing required field: name", "Add 'name: ...' to frontmatter"))

    if "description" not in fields:
        issues.append(Issue("error", str(filepath), 1, "AG-003",
                            "Missing required field: description", "Add 'description: ...'"))

    # Deprecated 'infer' field
    if "infer" in fields:
        line = fields["infer"]["line"]
        val = fields["infer"]["value"].lower()
        if val in ("true", "false"):
            new_fields = ""
            if val == "true":
                new_fields = "user-invocable: true"
            else:
                new_fields = "disable-model-invocation: true"
            issues.append(Issue("warning", str(filepath), line, "AG-004",
                                f"Deprecated field 'infer: {val}' — use '{new_fields}' instead",
                                f"Replace 'infer: {val}' with '{new_fields}'"))
        else:
            issues.append(Issue("error", str(filepath), line, "AG-005",
                                f"'infer' must be boolean (true/false), got '{val}'",
                                "Use 'infer: true' or 'infer: false'"))

    # Unknown fields
    for key, info in fields.items():
        if key not in AGENT_VALID_FIELDS:
            issues.append(Issue("warning", str(filepath), info["line"], "AG-006",
                                f"Unknown field '{key}' — will be silently ignored by CLI",
                                f"Remove '{key}' or check if you meant a valid field"))

    # Tool name validation
    if "tools" in fields:
        tools = parse_tools_list(fields["tools"]["value"])
        line = fields["tools"]["line"]

        for tool in tools:
            if is_vscode_tool(tool):
                issues.append(Issue("error", str(filepath), line, "AG-007",
                                    f"VS Code tool name '{tool}' — not valid in Copilot CLI",
                                    f"Use CLI equivalent (e.g., 'read', 'edit', 'search', 'bash')"))
            elif is_claude_tool(tool):
                issues.append(Issue("error", str(filepath), line, "AG-008",
                                    f"Claude Code tool name '{tool}' — not valid in Copilot CLI",
                                    f"Use CLI equivalent (lowercase: 'bash', 'read', 'edit')"))
            elif tool.lower() not in {t.lower() for t in VALID_CLI_TOOLS}:
                # Check if it looks like an MCP tool
                if not tool.startswith("mcp__"):
                    issues.append(Issue("warning", str(filepath), line, "AG-009",
                                        f"Unknown tool '{tool}' — may be ignored by CLI",
                                        f"Known tools: {', '.join(sorted(VALID_CLI_TOOLS))}"))

    # Schema confusion: skill fields used in agent
    if "allowed-tools" in fields:
        issues.append(Issue("error", str(filepath), fields["allowed-tools"]["line"], "AG-010",
                            "'allowed-tools' is a SKILL field — agents use 'tools' (array)",
                            "Rename 'allowed-tools' to 'tools' and use array syntax"))

    return issues


def lint_skill_file(filepath: Path, content: str) -> list[Issue]:
    """Lint a SKILL.md file against the Copilot CLI skill schema."""
    issues = []
    fields, _ = parse_frontmatter(content)

    if not fields:
        issues.append(Issue("error", str(filepath), 1, "SK-001",
                            "Missing YAML frontmatter (---)", "Add frontmatter block"))
        return issues

    # Required fields
    if "name" not in fields:
        issues.append(Issue("error", str(filepath), 1, "SK-002",
                            "Missing required field: name", "Add 'name: ...'"))

    if "description" not in fields:
        issues.append(Issue("error", str(filepath), 1, "SK-003",
                            "Missing required field: description", "Add 'description: ...'"))

    # Name constraints
    if "name" in fields:
        name_val = fields["name"]["value"].strip("'\"")
        if len(name_val) > 64:
            issues.append(Issue("error", str(filepath), fields["name"]["line"], "SK-004",
                                f"Skill name exceeds 64 chars ({len(name_val)} chars)",
                                "Shorten the name"))

    # Description constraints
    if "description" in fields:
        desc_val = fields["description"]["value"].strip("'\"")
        if not desc_val.startswith(">") and len(desc_val) > 1024:
            issues.append(Issue("warning", str(filepath), fields["description"]["line"], "SK-005",
                                f"Description exceeds 1024 chars ({len(desc_val)} chars)",
                                "Shorten the description"))

    # Deprecated 'infer' field (silently ignored in skills!)
    if "infer" in fields:
        issues.append(Issue("error", str(filepath), fields["infer"]["line"], "SK-006",
                            "'infer' is NOT a valid skill field — it's silently ignored. "
                            "Use 'user-invocable' and/or 'disable-model-invocation'",
                            "Remove 'infer' and use 'user-invocable: true/false'"))

    # Unknown fields (silently dropped by CLI for skills)
    for key, info in fields.items():
        if key not in SKILL_VALID_FIELDS:
            issues.append(Issue("warning", str(filepath), info["line"], "SK-007",
                                f"Unknown field '{key}' — silently ignored by CLI (skills use "
                                f"'onUnsupportedFields: ignore')",
                                f"Remove '{key}' — valid fields: {', '.join(sorted(SKILL_VALID_FIELDS))}"))

    # Schema confusion: agent fields used in skill
    if "tools" in fields:
        issues.append(Issue("error", str(filepath), fields["tools"]["line"], "SK-008",
                            "'tools' is an AGENT field — skills use 'allowed-tools' (string, comma-separated)",
                            "Rename 'tools' to 'allowed-tools' and use comma-separated string"))

    return issues


def collect_files(paths: list[str]) -> list[Path]:
    """Collect .agent.md and SKILL.md files from given paths."""
    files = []
    for p in paths:
        path = Path(p)
        if path.is_file():
            if path.name.endswith(".agent.md") or path.name == "SKILL.md":
                files.append(path)
        elif path.is_dir():
            for f in sorted(path.rglob("*.agent.md")):
                files.append(f)
            for f in sorted(path.rglob("SKILL.md")):
                files.append(f)
    return files


def get_default_scan_paths() -> list[str]:
    """Get default paths to scan."""
    tools_dir = Path(__file__).resolve().parent.parent
    paths = []

    # skills/ directory
    skills_dir = tools_dir / "skills"
    if skills_dir.exists():
        paths.append(str(skills_dir))

    # templates/ directory
    templates_dir = tools_dir / "templates"
    if templates_dir.exists():
        paths.append(str(templates_dir))

    return paths


def format_issue(issue: Issue, show_fix: bool = False) -> str:
    """Format an issue for terminal output."""
    icon = "❌" if issue.level == "error" else "⚠️"
    line = f"  {icon} [{issue.code}] {issue.file}:{issue.line} — {issue.message}"
    if show_fix and issue.fix:
        line += f"\n     💡 Fix: {issue.fix}"
    return line


def main():
    args = sys.argv[1:]
    show_fix = "--fix" in args
    scan_all = "--all" in args
    quiet = "--quiet" in args
    args = [a for a in args if a not in ("--fix", "--all", "--quiet", "--help", "-h")]

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    # Determine files to scan
    if args:
        files = collect_files(args)
    elif scan_all:
        files = collect_files(get_default_scan_paths())
    else:
        # Git pre-commit mode: scan staged files + default paths
        files = collect_files(get_default_scan_paths())

    if not files:
        if not quiet:
            print("No .agent.md or SKILL.md files found to scan.")
        sys.exit(0)

    # Lint all files
    all_issues: list[Issue] = []
    files_checked = 0

    for filepath in files:
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            all_issues.append(Issue("error", str(filepath), 0, "IO-001",
                                    f"Cannot read file: {e}", ""))
            continue

        files_checked += 1
        if filepath.name.endswith(".agent.md"):
            all_issues.extend(lint_agent_file(filepath, content))
        elif filepath.name == "SKILL.md":
            all_issues.extend(lint_skill_file(filepath, content))

    # Report
    errors = [i for i in all_issues if i.level == "error"]
    warnings = [i for i in all_issues if i.level == "warning"]

    if not all_issues:
        if not quiet:
            ver = f" (CLI {_CLI_VERSION})" if _CLI_VERSION else ""
            src = "auto-parsed" if _auto_tools else "fallback"
            print(f"✅ {files_checked} files checked — all clean [{src}{ver}]")
        sys.exit(0)

    if not quiet:
        ver = f" (CLI {_CLI_VERSION})" if _CLI_VERSION else ""
        src = "auto-parsed" if _auto_tools else "fallback"
        print(f"\n🔍 Scanned {files_checked} files [{src}{ver}]\n")

    for issue in sorted(all_issues, key=lambda i: (i.file, i.line)):
        print(format_issue(issue, show_fix))

    print(f"\n{'─' * 60}")
    print(f"Found {len(errors)} error(s), {len(warnings)} warning(s)")

    if show_fix and all_issues:
        print("\n💡 Run suggested fixes manually or use:")
        print("   python3 lint-skills.py --fix --all")

    if errors:
        sys.exit(1)
    elif warnings:
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
