#!/usr/bin/env python3
"""
project-context.py — Generate a durable project constitution artifact

Combines repo structure, detected profile conventions, hooks metadata, and
testing expectations into a single grounded project-context.md.  No AI
generation, no network access, no speculative runtime changes required.

Usage:
    python3 project-context.py                      # Write to session files/ dir
    python3 project-context.py --stdout             # Print to stdout only
    python3 project-context.py --output PATH        # Write to explicit path
    python3 project-context.py --repo PATH          # Use a different repo root
    python3 project-context.py --profile PROFILE    # Force a preset profile
    python3 project-context.py --no-write           # Dry-run: show target path
    python3 project-context.py --list-profiles      # Show available profiles
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# Fix Windows console encoding
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parent
PRESETS_DIR = TOOLS_DIR / "presets"
SESSION_STATE = Path.home() / ".copilot" / "session-state"

# Profile detection: ordered list of (profile_name, indicator_files/dirs)
# NOTE: build.gradle / build.gradle.kts / Podfile are intentionally absent from
# mobile indicators — they are common in JVM server-side and Swift server repos
# and produce widespread false positives.  Only top-level directories that are
# unambiguously mobile-native (android/, ios/) and *.xcodeproj entries qualify.
_PROFILE_INDICATORS: list[tuple[str, list[str]]] = [
    ("mobile", ["android", "ios", "*.xcodeproj"]),
    ("fullstack", ["frontend", "backend", "client", "server", "api"]),
    ("typescript", ["package.json", "tsconfig.json", "yarn.lock", "pnpm-lock.yaml"]),
    ("python", ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile", "poetry.lock"]),
]


# ─── Git helpers ─────────────────────────────────────────────────────────────

def find_git_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def ls_files(repo_root: Path, timeout: int = 10) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(repo_root),
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def get_git_last_commit_date(repo_root: Path) -> str:
    """Return ISO 8601 date of the last commit; empty string on failure.

    Using the commit date rather than wall-clock time keeps the artifact
    deterministic: identical repo state → identical output.
    """
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%cI"],
            capture_output=True, text=True, timeout=5, cwd=str(repo_root),
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def get_git_remote(repo_root: Path) -> str:
    """Return the primary git remote URL, or empty string."""
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5, cwd=str(repo_root),
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def get_git_branch(repo_root: Path) -> str:
    """Return the current branch name."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(repo_root),
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


# ─── Profile detection ────────────────────────────────────────────────────────

def _glob_match_any(name: str, patterns: list[str]) -> bool:
    """Simple glob: support leading '*.' suffix patterns and exact names."""
    for pat in patterns:
        if pat.startswith("*"):
            if name.endswith(pat[1:]):
                return True
        elif name == pat:
            return True
    return False


def detect_profile(repo_root: Path, files: list[str]) -> str:
    """Heuristically determine the best-fit profile for the repo."""
    # Collect top-level names (files and dirs)
    top_level: set[str] = set()
    for f in files:
        parts = Path(f).parts
        top_level.add(parts[0])

    # Also look at actual filesystem entries not just tracked files
    try:
        top_level.update(p.name for p in repo_root.iterdir())
    except Exception:
        pass

    # Check indicators in priority order
    for profile_name, indicators in _PROFILE_INDICATORS:
        for ind in indicators:
            if _glob_match_any(ind, [ind]) and ind in top_level:
                return profile_name
            # glob pattern check
            if ind.startswith("*."):
                ext = ind[1:]
                if any(n.endswith(ext) for n in top_level):
                    return profile_name

    return "default"


def load_preset(profile_name: str) -> dict:
    """Load and return a preset JSON dict. Falls back to default on missing."""
    path = PRESETS_DIR / f"{profile_name}.json"
    if not path.exists():
        path = PRESETS_DIR / "default.json"
    if not path.exists():
        return {
            "name": profile_name,
            "description": "",
            "hooks": [],
            "workflow_phases": ["CLARIFY", "BUILD", "TEST", "COMMIT"],
            "workflow_notes": "",
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def list_profiles() -> list[str]:
    """Return all available profile names (filenames without extension)."""
    if not PRESETS_DIR.exists():
        return []
    return sorted(p.stem for p in PRESETS_DIR.glob("*.json"))


# ─── File structure analysis ──────────────────────────────────────────────────

def group_files(files: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for f in sorted(files):
        parts = Path(f).parts
        top = parts[0] if len(parts) > 1 else "."
        groups[top].append(f)
    return dict(sorted(groups.items()))


def ext_summary(files: list[str], cap: int = 6) -> str:
    counts: dict[str, int] = defaultdict(int)
    for f in files:
        ext = Path(f).suffix or "(no ext)"
        counts[ext] += 1
    parts = [f"{ext}×{n}" if n > 1 else ext for ext, n in sorted(counts.items())]
    tail = f", +{len(parts) - cap} more" if len(parts) > cap else ""
    return ", ".join(parts[:cap]) + tail


def find_test_files(files: list[str]) -> list[str]:
    """Return tracked test-related files (heuristic)."""
    return [
        f for f in files
        if (
            "test" in Path(f).name.lower()
            or "spec" in Path(f).name.lower()
            or Path(f).parts[0] in ("tests", "test", "__tests__", "spec")
        )
    ]


def find_config_files(files: list[str]) -> list[str]:
    """Return well-known config/entrypoint files."""
    important = {
        "package.json", "pyproject.toml", "setup.py", "requirements.txt",
        "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        ".github", "tsconfig.json", "build.gradle", "build.gradle.kts",
        "Podfile", "Cargo.toml", "go.mod", "pom.xml",
        "AGENTS.md", "WORKFLOW.md", "README.md", ".copilot",
    }
    return [f for f in files if Path(f).name in important or Path(f).parts[0] in important]


# ─── Artifact generation ──────────────────────────────────────────────────────

def generate_context(
    repo_root: Path,
    files: list[str],
    preset: dict,
    detected_profile: str,
    forced_profile: bool,
) -> str:
    """Return the full project-context.md as a string."""
    commit_date = get_git_last_commit_date(repo_root)
    repo_name = repo_root.name
    remote = get_git_remote(repo_root)
    branch = get_git_branch(repo_root)

    profile_name = preset.get("name", detected_profile)
    profile_desc = preset.get("description", "")
    phases = preset.get("workflow_phases", [])
    workflow_notes = preset.get("workflow_notes", "")
    hooks = preset.get("hooks", [])

    groups = group_files(files)
    test_files = find_test_files(files)
    config_files = find_config_files(files)

    profile_source = "forced" if forced_profile else "auto-detected"

    lines: list[str] = [
        f"# Project Context — {repo_name}",
        "",
        "> Auto-generated by project-context.py — do not edit manually.",
        "",
        f"Last commit: {commit_date}  " if commit_date else "",
        f"Repository root: `{repo_root}`  ",
        f"Branch: `{branch}`  " if branch else "",
        f"Remote: {remote}  " if remote else "",
        f"Profile: **{profile_name}** ({profile_source})  ",
        f"Total tracked files: {len(files)}",
        "",
    ]
    # Remove empty lines caused by optional fields
    lines = [l for l in lines if l != ""]

    # ── Identity ──────────────────────────────────────────────────────────────
    lines += [
        "",
        "## Identity",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| Repo name | `{repo_name}` |",
        f"| Root | `{repo_root}` |",
        f"| Branch | `{branch}` |" if branch else "",
        f"| Remote | {remote} |" if remote else "",
        f"| Profile | {profile_name} ({profile_source}) |",
        f"| Files tracked | {len(files)} |",
        "",
    ]
    lines = [l for l in lines if l != ""]

    # ── Profile description ───────────────────────────────────────────────────
    if profile_desc:
        lines += [
            "",
            "## Profile",
            "",
            profile_desc,
            "",
        ]

    # ── Workflow ──────────────────────────────────────────────────────────────
    lines += [
        "",
        "## Workflow Phases",
        "",
    ]
    if phases:
        phase_chain = " → ".join(phases)
        lines.append(f"**{phase_chain}**")
        lines.append("")
        if workflow_notes:
            lines.append(workflow_notes)
            lines.append("")

        lines += [
            "| # | Phase | Role |",
            "|---|-------|------|",
        ]
        phase_roles = {
            "CLARIFY": "Make requirements implementation-ready",
            "DESIGN": "Generate visual/technical design",
            "VERIFY": "Review design before coding",
            "BUILD": "Implement code",
            "TEST": "Functional verification — all tests must pass",
            "REVIEW": "Code quality check and approval",
            "QA": "Visual/manual verification with evidence",
            "COMMIT": "Ship — clean git commit with trailer",
        }
        for i, phase in enumerate(phases):
            role = phase_roles.get(phase, "")
            lines.append(f"| {i} | **{phase}** | {role} |")
        lines.append("")
    else:
        lines.append("No phases defined in preset.")
        lines.append("")

    # ── Quality gates / hooks ─────────────────────────────────────────────────
    lines += [
        "",
        "## Quality Gates & Hooks",
        "",
    ]
    if hooks:
        lines.append("Active hooks enforced by this profile:")
        lines.append("")
        hook_docs = {
            "dangerous-blocker.sh": "Blocks destructive shell commands (rm -rf, etc.)",
            "secret-detector.sh": "Blocks commits containing API keys or credentials",
            "test-reminder.sh": "Reminds to run tests before committing",
            "build-reminder.sh": "Reminds to verify the build before committing",
            "enforce-tdd-pipeline.sh": "Enforces red→green→refactor TDD cycle",
            "commit-gate.sh": "Blocks commits unless all quality gates pass",
            "enforce-coding-standards.sh": "Enforces language-specific coding conventions",
            "architecture-guard.sh": "Prevents cross-layer dependency violations",
            "session-banner.sh": "Displays project context at session start",
        }
        for hook in hooks:
            doc = hook_docs.get(hook, "")
            entry = f"- `{hook}`" + (f" — {doc}" if doc else "")
            lines.append(entry)
        lines.append("")
    else:
        lines.append("No hooks defined in this profile.")
        lines.append("")

    # ── Testing conventions ───────────────────────────────────────────────────
    lines += [
        "",
        "## Testing Conventions",
        "",
    ]
    if test_files:
        lines.append(f"**{len(test_files)} test file(s) found:**")
        lines.append("")
        for tf in sorted(test_files)[:20]:  # cap at 20 for readability
            lines.append(f"- `{tf}`")
        if len(test_files) > 20:
            lines.append(f"- … and {len(test_files) - 20} more")
        lines.append("")
        lines.append("> Run tests before every commit. Do NOT mark tasks complete until tests pass.")
    else:
        lines.append("No test files detected. Add tests before shipping.")
    lines.append("")

    # ── Key config files ──────────────────────────────────────────────────────
    if config_files:
        lines += [
            "",
            "## Key Configuration Files",
            "",
        ]
        for cf in sorted(set(config_files))[:30]:
            lines.append(f"- `{cf}`")
        lines.append("")

    # ── File structure summary ────────────────────────────────────────────────
    lines += [
        "",
        "## File Structure",
        "",
        f"Total: **{len(files)} tracked files** across **{len(groups)} top-level director{'y' if len(groups)==1 else 'ies'}**",
        "",
        "| Directory | Files | Extensions |",
        "|-----------|-------|------------|",
    ]
    for group, gfiles in groups.items():
        label = f"`{group}/`" if group != "." else "`./` (root)"
        lines.append(f"| {label} | {len(gfiles)} | {ext_summary(gfiles)} |")
    lines.append("")

    # ── Footer ────────────────────────────────────────────────────────────────
    lines += [
        "",
        "---",
        "",
        "*Auto-generated by `project-context.py` — do not edit manually.*  ",
        f"*Regenerate with: `python3 {TOOLS_DIR}/project-context.py`*",
    ]

    return "\n".join(lines) + "\n"


# ─── Session output ───────────────────────────────────────────────────────────

def get_session_files_dir() -> Path | None:
    if not SESSION_STATE.exists():
        return None
    sessions = sorted(
        (d for d in SESSION_STATE.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if not sessions:
        return None
    files_dir = sessions[0] / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    return files_dir


def resolve_output_path(args_output: str | None) -> Path | None:
    if args_output:
        return Path(args_output).resolve()
    files_dir = get_session_files_dir()
    if files_dir:
        return files_dir / "project-context.md"
    return None


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate project-context.md — a durable project constitution artifact."
    )
    parser.add_argument("--stdout", action="store_true",
                        help="Print context to stdout instead of writing a file.")
    parser.add_argument("--output", metavar="PATH",
                        help="Write to an explicit file path.")
    parser.add_argument("--repo", metavar="PATH",
                        help="Repository root (defaults to git root of cwd).")
    parser.add_argument("--profile", metavar="NAME",
                        help="Force a specific preset profile (overrides auto-detection).")
    parser.add_argument("--no-write", action="store_true",
                        help="Dry-run: show the target path without writing.")
    parser.add_argument("--list-profiles", action="store_true",
                        help="List available profiles and exit.")
    args = parser.parse_args()

    if args.list_profiles:
        profiles = list_profiles()
        if not profiles:
            print("No profiles found in presets/", file=sys.stderr)
            return 1
        print("Available profiles:")
        for p in profiles:
            preset = load_preset(p)
            desc = preset.get("description", "")
            print(f"  {p:<14} — {desc}")
        return 0

    repo_root = Path(args.repo).resolve() if args.repo else find_git_root()
    if repo_root is None:
        print("Error: not in a git repository. Use --repo to specify root.", file=sys.stderr)
        return 1

    files = ls_files(repo_root)
    if not files:
        print(f"Warning: no tracked files found in {repo_root}.", file=sys.stderr)

    # Profile resolution
    forced_profile = args.profile is not None
    if forced_profile:
        profile_name = args.profile
        available = list_profiles()
        if profile_name not in available:
            print(
                f"Error: unknown profile '{profile_name}'. "
                f"Available: {', '.join(available)}",
                file=sys.stderr,
            )
            return 1
    else:
        profile_name = detect_profile(repo_root, files)

    preset = load_preset(profile_name)
    content = generate_context(repo_root, files, preset, profile_name, forced_profile)

    if args.stdout:
        sys.stdout.write(content)
        return 0

    out_path = resolve_output_path(args.output)
    if out_path is None:
        print(
            "Error: no active Copilot session found and no --output given. "
            "Use --output PATH or --stdout.",
            file=sys.stderr,
        )
        return 1

    if args.no_write:
        print(f"Would write to: {out_path}")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(f"✅ project-context.md → {out_path}")
    print(f"   Profile: {profile_name} ({'forced' if forced_profile else 'auto-detected'})")
    print(f"   {len(files)} tracked files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
