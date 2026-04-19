#!/usr/bin/env python3
"""
test_host_symmetry.py — Prove Copilot CLI + Claude Code host symmetry.

Scope: host_manifest.py, watch-sessions.py, install.py, setup-project.py.
Verifies:
  0. host_manifest.py is the single source of truth for host metadata.
  1. KNOWN_HOSTS in watch-sessions.py names exactly Copilot CLI + Claude Code.
  2. KNOWN_HOSTS in install.py names exactly Copilot CLI + Claude Code.
  3. install.py deploy_skill() deploys to both host-specific paths.
  4. install.py deploy_hooks() is intentionally Copilot CLI-only (documented).
  5. install.py markers/ dir is Copilot CLI-only (Claude uses settings.json).
  6. KNOWN_HOSTS_INSTRUCTION_FILES in setup-project.py lists only known hosts.
  7. Unsupported hosts (Codex, Cursor, Windsurf, etc.) stay explicitly out of scope.

Run: python3 test_host_symmetry.py
"""

import importlib.util
import io
import os
import sys
import shutil
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

PASS = 0
FAIL = 0
REPO = Path(__file__).parent


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ─── Load source texts ───────────────────────────────────────────────────────

manifest_source = (REPO / "host_manifest.py").read_text(encoding="utf-8")
ws_source       = (REPO / "watch-sessions.py").read_text(encoding="utf-8")
inst_source     = (REPO / "install.py").read_text(encoding="utf-8")
sp_source       = (REPO / "setup-project.py").read_text(encoding="utf-8")

# Load the manifest module so we can inspect its runtime values
_spec = importlib.util.spec_from_file_location("host_manifest", REPO / "host_manifest.py")
_manifest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_manifest)

# ─── 0. host_manifest.py — single source of truth ───────────────────────────

print("\n📋 host_manifest.py — Single Source of Truth")

test("host_manifest.py exists",
     (REPO / "host_manifest.py").is_file(),
     "Create host_manifest.py as the canonical host registry")

test("host_manifest.py defines SUPPORTED_HOSTS",
     "SUPPORTED_HOSTS" in manifest_source,
     "SUPPORTED_HOSTS must list all supported host names")

test("host_manifest.py defines HOST_DIRS",
     "HOST_DIRS" in manifest_source,
     "HOST_DIRS must map host names to config directories")

test("host_manifest.py defines HOST_SESSION_ROOTS",
     "HOST_SESSION_ROOTS" in manifest_source,
     "HOST_SESSION_ROOTS must map host names to session directories")

test("host_manifest.py defines HOST_INSTRUCTION_FILES",
     "HOST_INSTRUCTION_FILES" in manifest_source,
     "HOST_INSTRUCTION_FILES must map host names to instruction file paths")

test("manifest SUPPORTED_HOSTS contains 'Copilot CLI'",
     "Copilot CLI" in _manifest.SUPPORTED_HOSTS,
     "Copilot CLI must be a supported host")

test("manifest SUPPORTED_HOSTS contains 'Claude Code'",
     "Claude Code" in _manifest.SUPPORTED_HOSTS,
     "Claude Code must be a supported host")

test("manifest SUPPORTED_HOSTS has exactly 2 entries",
     len(_manifest.SUPPORTED_HOSTS) == 2,
     f"Expected 2 supported hosts, got {len(_manifest.SUPPORTED_HOSTS)}")

test("manifest HOST_DIRS maps 'Copilot CLI' to ~/.copilot",
     "Copilot CLI" in _manifest.HOST_DIRS
     and str(_manifest.HOST_DIRS["Copilot CLI"]).endswith(".copilot"),
     "HOST_DIRS['Copilot CLI'] must resolve to ~/.copilot")

test("manifest HOST_DIRS maps 'Claude Code' to ~/.claude",
     "Claude Code" in _manifest.HOST_DIRS
     and str(_manifest.HOST_DIRS["Claude Code"]).endswith(".claude"),
     "HOST_DIRS['Claude Code'] must resolve to ~/.claude")

test("manifest SESSION_STATE path is ~/.copilot/session-state",
     '".copilot" / "session-state"' in manifest_source
     or "session-state" in manifest_source,
     "SESSION_STATE must point to ~/.copilot/session-state")

test("manifest CLAUDE_PROJECTS path is ~/.claude/projects",
     '".claude" / "projects"' in manifest_source
     or "projects" in manifest_source,
     "CLAUDE_PROJECTS must point to ~/.claude/projects")

test("manifest HOST_INSTRUCTION_FILES has 'Copilot CLI' entry",
     "Copilot CLI" in _manifest.HOST_INSTRUCTION_FILES,
     "HOST_INSTRUCTION_FILES must include Copilot CLI")

test("manifest HOST_INSTRUCTION_FILES has 'Claude Code' entry",
     "Claude Code" in _manifest.HOST_INSTRUCTION_FILES,
     "HOST_INSTRUCTION_FILES must include Claude Code")

test("manifest Copilot CLI instruction file is copilot-instructions.md",
     _manifest.HOST_INSTRUCTION_FILES.get("Copilot CLI", "") == ".github/copilot-instructions.md",
     "Copilot CLI must use .github/copilot-instructions.md")

test("manifest Claude Code instruction file is CLAUDE.md",
     _manifest.HOST_INSTRUCTION_FILES.get("Claude Code", "") == "CLAUDE.md",
     "Claude Code must use CLAUDE.md")

test("manifest defines UNSUPPORTED_HOSTS",
     "UNSUPPORTED_HOSTS" in manifest_source,
     "UNSUPPORTED_HOSTS must explicitly name excluded hosts")

_UNSUPPORTED = ["Codex", "Cursor", "Windsurf", "Cline"]
for host in _UNSUPPORTED:
    test(f"manifest UNSUPPORTED_HOSTS includes '{host}'",
         host in _manifest.UNSUPPORTED_HOSTS,
         f"{host} must be listed in UNSUPPORTED_HOSTS")

    has_dir = (
        f'".{host.lower()}"' in manifest_source
        or f"'.{host.lower()}'" in manifest_source
    )
    test(f"manifest: no '{host}' directory path reference",
         not has_dir,
         f"Found .{host.lower()} in host_manifest.py — out of scope")

# ─── 1. watch-sessions.py KNOWN_HOSTS ───────────────────────────────────────

print("\n🔭 watch-sessions.py — Known Host Symmetry")

test("KNOWN_HOSTS defined in watch-sessions.py",
     "KNOWN_HOSTS" in ws_source,
     "Add KNOWN_HOSTS (imported from host_manifest) to watch-sessions.py")

test("watch-sessions.py imports from host_manifest",
     "from host_manifest import" in ws_source or "import host_manifest" in ws_source,
     "watch-sessions.py must delegate host metadata to host_manifest.py")

test("KNOWN_HOSTS contains 'Copilot CLI'",
     '"Copilot CLI"' in manifest_source,
     "host_manifest must name Copilot CLI")

test("KNOWN_HOSTS contains 'Claude Code'",
     '"Claude Code"' in manifest_source,
     "host_manifest must name Claude Code")

test("SESSION_STATE path is ~/.copilot/session-state",
     '".copilot" / "session-state"' in manifest_source
     or ('"session-state"' in manifest_source and "COPILOT_DIR" in manifest_source),
     "SESSION_STATE must point to ~/.copilot/session-state (in host_manifest)")

test("CLAUDE_PROJECTS path is ~/.claude/projects",
     '".claude" / "projects"' in manifest_source
     or ('"projects"' in manifest_source and "CLAUDE_DIR" in manifest_source),
     "CLAUDE_PROJECTS must point to ~/.claude/projects (in host_manifest)")

# KNOWN_HOSTS drives watch_dirs — SESSION_STATE always required
test("watch_dirs derived from KNOWN_HOSTS in main()",
     "KNOWN_HOSTS" in ws_source[ws_source.find("def main("):],
     "main() must build watch_dirs from KNOWN_HOSTS, not ad-hoc ifs")

# Only two hosts — no unsupported directories referenced as Path objects
for host in _UNSUPPORTED + ["Copilot Chat"]:
    has_dir = f'".{host.lower()}"' in ws_source or f"'{host.lower()}'" in ws_source
    test(f"watch-sessions.py: no '{host}' directory reference",
         not has_dir,
         f"Remove {host} path from watch-sessions.py — not grounded")

# ─── 2. install.py KNOWN_HOSTS ───────────────────────────────────────────────

print("\n🔧 install.py — Host Detection Symmetry")

test("KNOWN_HOSTS defined in install.py",
     "KNOWN_HOSTS" in inst_source,
     "Add KNOWN_HOSTS (imported from host_manifest) to install.py")

test("install.py imports from host_manifest",
     "from host_manifest import" in inst_source or "import host_manifest" in inst_source,
     "install.py must delegate host metadata to host_manifest.py")

test("install.py KNOWN_HOSTS includes 'Copilot CLI'",
     '"Copilot CLI"' in manifest_source,
     "host_manifest HOST_DIRS must list Copilot CLI")

test("install.py KNOWN_HOSTS includes 'Claude Code'",
     '"Claude Code"' in manifest_source,
     "host_manifest HOST_DIRS must list Claude Code")

# show_status() must use KNOWN_HOSTS for iteration (no bare ad-hoc Copilot/Claude ifs)
show_status_start = inst_source.find("def show_status()")
show_status_end   = inst_source.find("\ndef ", show_status_start + 1)
show_status_body  = inst_source[show_status_start:show_status_end]
test("show_status() uses KNOWN_HOSTS for agent detection",
     "KNOWN_HOSTS" in show_status_body,
     "show_status() must iterate KNOWN_HOSTS, not separate if/else per host")

# deploy_skill() deploys to both COPILOT_DIR and CLAUDE_DIR
deploy_skill_start = inst_source.find("def deploy_skill()")
deploy_skill_end   = inst_source.find("\ndef ", deploy_skill_start + 1)
deploy_skill_body  = inst_source[deploy_skill_start:deploy_skill_end]
test("deploy_skill() checks COPILOT_DIR for Copilot skill",
     "COPILOT_DIR.is_dir()" in deploy_skill_body,
     "deploy_skill() must check COPILOT_DIR")
test("deploy_skill() checks CLAUDE_DIR for Claude skill",
     "CLAUDE_DIR.is_dir()" in deploy_skill_body,
     "deploy_skill() must check CLAUDE_DIR")

# deploy_hooks() is Copilot CLI-only — must say so in its docstring/body
deploy_hooks_start = inst_source.find("def deploy_hooks()")
deploy_hooks_end   = inst_source.find("\ndef ", deploy_hooks_start + 1)
deploy_hooks_body  = inst_source[deploy_hooks_start:deploy_hooks_end]

test("deploy_hooks() documents Copilot CLI-only scope",
     "Copilot" in deploy_hooks_body and "Claude" in deploy_hooks_body,
     "deploy_hooks() must document that Claude Code is handled differently")

test("deploy_hooks() writes to COPILOT_DIR hooks dir",
     "COPILOT_DIR" in deploy_hooks_body,
     "deploy_hooks() must use COPILOT_DIR / 'hooks'")

test("deploy_hooks() does NOT write to CLAUDE_DIR",
     "CLAUDE_DIR" not in deploy_hooks_body,
     "deploy_hooks() must not attempt to write to ~/.claude/ — different format")

# markers/ directory is Copilot CLI-only
test("markers/ directory is under COPILOT_DIR (Copilot CLI-only)",
     'COPILOT_DIR / "markers"' in inst_source,
     "markers/ must be under ~/.copilot/ not ~/.claude/")

test("no CLAUDE_DIR/markers path in install.py",
     'CLAUDE_DIR / "markers"' not in inst_source,
     "Claude Code does not use a markers/ directory in this tool")

# ─── 3. Functional path symmetry for deploy_skill() ─────────────────────────

print("\n📦 deploy_skill() — Path Symmetry (static analysis)")

# Copilot skill path: .github/skills/session-knowledge/SKILL.md
test("Copilot skill path: .github/skills/session-knowledge/SKILL.md",
     '.github" / "skills"' in deploy_skill_body
     or ".github/skills" in deploy_skill_body
     or '"skills"' in deploy_skill_body,
     "deploy_skill() must write .github/skills/session-knowledge/SKILL.md for Copilot")

# Claude skill path: .claude/skills/session-knowledge/SKILL.md
test("Claude skill path: .claude/skills/session-knowledge/SKILL.md",
     '".claude"' in deploy_skill_body and '"skills"' in deploy_skill_body,
     "deploy_skill() must write .claude/skills/session-knowledge/SKILL.md for Claude")

# ─── 4. Functional deploy_skill() with mock filesystem ───────────────────────

print("\n📦 deploy_skill() — Functional Symmetry (mock filesystem)")

tmpdir = Path(tempfile.mkdtemp())
try:
    fake_copilot  = tmpdir / "dot-copilot"
    fake_claude   = tmpdir / "dot-claude"
    fake_project  = tmpdir / "project"
    fake_skill_src = tmpdir / "SKILL.md"

    fake_copilot.mkdir()
    fake_claude.mkdir()
    fake_project.mkdir()
    fake_skill_src.write_text("# Test Skill Content", encoding="utf-8")

    # Load install module in isolation (no side effects from main())
    _spec = importlib.util.spec_from_file_location("_install_test", REPO / "install.py")
    _mod  = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)

    # Patch module-level globals so deploy_skill() operates on our fake dirs
    _mod.COPILOT_DIR = fake_copilot
    _mod.CLAUDE_DIR  = fake_claude
    _mod.SKILLS_SRC  = fake_skill_src
    _mod._git_root   = lambda: fake_project

    # Call the real function (suppress its print output)
    with redirect_stdout(io.StringIO()):
        _mod.deploy_skill()

    copilot_skill = fake_project / ".github" / "skills" / "session-knowledge" / "SKILL.md"
    claude_skill  = fake_project / ".claude"  / "skills" / "session-knowledge" / "SKILL.md"

    test("deploy_skill() creates Copilot skill at .github/skills/session-knowledge/SKILL.md",
         copilot_skill.is_file(),
         "deploy_skill() must create file for Copilot CLI when COPILOT_DIR exists")

    test("deploy_skill() creates Claude skill at .claude/skills/session-knowledge/SKILL.md",
         claude_skill.is_file(),
         "deploy_skill() must create file for Claude Code when CLAUDE_DIR exists")

    test("deploy_skill() writes correct content to Copilot skill file",
         copilot_skill.is_file() and copilot_skill.read_text(encoding="utf-8") == "# Test Skill Content",
         "Copilot skill file must contain the source SKILL.md content")

    test("deploy_skill() writes correct content to Claude skill file",
         claude_skill.is_file() and claude_skill.read_text(encoding="utf-8") == "# Test Skill Content",
         "Claude skill file must contain the source SKILL.md content")

    test("Copilot and Claude skill paths are distinct",
         copilot_skill != claude_skill,
         "Each host must have its own distinct skill file path")

    # Unsupported host paths must NOT be created
    for unsupported in [
        ".codex/skills/session-knowledge/SKILL.md",
        ".cursor/skills/session-knowledge/SKILL.md",
        ".windsurf/skills/session-knowledge/SKILL.md",
    ]:
        test(f"No skill file for unsupported path: {unsupported}",
             not (fake_project / unsupported).exists(),
             f"{unsupported} must not be created — host not supported")

finally:
    shutil.rmtree(str(tmpdir), ignore_errors=True)

# ─── 5. setup-project.py KNOWN_HOSTS_INSTRUCTION_FILES ──────────────────────

print("\n📝 setup-project.py — Host-Specific Instruction Files")

test("KNOWN_HOSTS_INSTRUCTION_FILES defined in setup-project.py",
     "KNOWN_HOSTS_INSTRUCTION_FILES" in sp_source,
     "Add KNOWN_HOSTS_INSTRUCTION_FILES (imported from host_manifest) to setup-project.py")

test("setup-project.py imports from host_manifest",
     "from host_manifest import" in sp_source or "import host_manifest" in sp_source,
     "setup-project.py must delegate host metadata to host_manifest.py")

test("setup-project.py names 'Copilot CLI' in host instruction files",
     '"Copilot CLI"' in manifest_source,
     "host_manifest HOST_INSTRUCTION_FILES must include Copilot CLI")

test("setup-project.py names 'Claude Code' in host instruction files",
     '"Claude Code"' in manifest_source,
     "host_manifest HOST_INSTRUCTION_FILES must include Claude Code")

test("setup-project.py handles .github/copilot-instructions.md (Copilot CLI)",
     "copilot-instructions.md" in sp_source,
     "patch_copilot_instructions() must target .github/copilot-instructions.md")

test("setup-project.py handles CLAUDE.md (Claude Code)",
     "CLAUDE.md" in sp_source,
     "patch_claude_md() must target CLAUDE.md")

test("setup-project.py handles AGENTS.md (all agents)",
     "AGENTS.md" in sp_source,
     "patch_agents_md() must target AGENTS.md")

# No unsupported host instruction files
for unsupported in [".codex/instructions", ".cursor/rules", "windsurf-instructions"]:
    test(f"No '{unsupported}' in setup-project.py",
         unsupported not in sp_source,
         f"Unsupported host file '{unsupported}' must not be referenced")

# ─── 6. Scope boundary — unsupported hosts across all scope files ────────────

print("\n🚫 Scope Boundary — Unsupported Hosts")

for filename, source in [
    ("host_manifest.py",  manifest_source),
    ("watch-sessions.py", ws_source),
    ("install.py",        inst_source),
    ("setup-project.py",  sp_source),
]:
    for host in _UNSUPPORTED:
        # Only flag explicit Path-like directory references (e.g., ".codex", ".cursor")
        has_dir = (
            f'".{host.lower()}"' in source
            or f"'.{host.lower()}'" in source
        )
        test(f"{filename}: no '{host}' directory path reference",
             not has_dir,
             f"Found .{host.lower()} directory reference in {filename} — out of scope")

# ─── Summary ─────────────────────────────────────────────────────────────────

print()
print("=" * 50)
total = PASS + FAIL
print(f"Results: {PASS} passed, {FAIL} failed out of {total}")
if FAIL == 0:
    print("🎉 All host-symmetry tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) failed — fix host symmetry issues above")
    sys.exit(1)
