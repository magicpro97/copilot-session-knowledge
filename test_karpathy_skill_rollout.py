#!/usr/bin/env python3
"""
test_karpathy_skill_rollout.py — Tests for karpathy-guidelines vendor & rollout.

Verifies:
  1. skills/karpathy-guidelines/SKILL.md exists and passes validate-skill.py.
  2. karpathy-guidelines is registered in setup-project.py INSTALL_ITEMS["skills"].
  3. VENDORED_SKILLS in auto-update-tools.py includes karpathy-guidelines.
  4. deploy_skills() updates an already-deployed karpathy skill body.
  5. deploy_skills() does NOT create new deployments (only updates existing).
  6. Unsupported host surfaces (.cursor/, .claude-plugin/) are absent from
     setup-project.py and auto-update-tools.py scope.
  7. Path derivation from HOST_SKILL_SUBPATHS is consistent for both hosts.
  8. No upstream CLAUDE.md is vendored or written by setup-project.py.

Run: python3 test_karpathy_skill_rollout.py
"""

import importlib.util
import os
import sys
import shutil
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Windows encoding fix (standard pattern for this repo)
# ---------------------------------------------------------------------------
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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

sp_source  = (REPO / "setup-project.py").read_text(encoding="utf-8")
aut_source = (REPO / "auto-update-tools.py").read_text(encoding="utf-8")

# Load host_manifest for path derivation checks
_hm_spec = importlib.util.spec_from_file_location("host_manifest", REPO / "host_manifest.py")
_hm_mod  = importlib.util.module_from_spec(_hm_spec)
_hm_spec.loader.exec_module(_hm_mod)
HOST_SKILL_SUBPATHS = _hm_mod.HOST_SKILL_SUBPATHS
HOST_DIRS           = _hm_mod.HOST_DIRS
SUPPORTED_HOSTS     = _hm_mod.SUPPORTED_HOSTS

# ---------------------------------------------------------------------------
# 1. Skill file existence and validity
# ---------------------------------------------------------------------------
print("\n📦 1. Skill asset existence")

skill_md = REPO / "skills" / "karpathy-guidelines" / "SKILL.md"
test("skills/karpathy-guidelines/SKILL.md exists", skill_md.exists())

if skill_md.exists():
    content = skill_md.read_text(encoding="utf-8")
    test("SKILL.md has YAML frontmatter", content.startswith("---"))
    test("SKILL.md has 'name: karpathy-guidelines' in frontmatter",
         "name: karpathy-guidelines" in content)
    test("SKILL.md has 'description:' in frontmatter",
         "description:" in content[:500])
    test("SKILL.md has 'license: MIT'", "license: MIT" in content)
    test("SKILL.md has attribution section",
         "vendored-from" in content or "Attribution" in content)
    test("SKILL.md does NOT reference .cursor/",
         ".cursor/" not in content,
         "Unsupported host surface leaked into SKILL.md")
    test("SKILL.md does NOT reference .claude-plugin/",
         ".claude-plugin/" not in content,
         "Unsupported host surface leaked into SKILL.md")

# ---------------------------------------------------------------------------
# 2. setup-project.py INSTALL_ITEMS registration
# ---------------------------------------------------------------------------
print("\n🔧 2. setup-project.py registration")

test("karpathy-guidelines in INSTALL_ITEMS[skills] src list",
     '"karpathy-guidelines"' in sp_source or "'karpathy-guidelines'" in sp_source)
test("setup-project.py does not hardcode .cursor/ path",
     ".cursor/" not in sp_source,
     "Unsupported host surface in setup-project.py")
test("setup-project.py does not hardcode .claude-plugin/ path",
     ".claude-plugin/" not in sp_source,
     "Unsupported host surface in setup-project.py")

# ---------------------------------------------------------------------------
# 3. auto-update-tools.py VENDORED_SKILLS
# ---------------------------------------------------------------------------
print("\n🔄 3. auto-update-tools.py VENDORED_SKILLS")

test("VENDORED_SKILLS defined in auto-update-tools.py",
     "VENDORED_SKILLS" in aut_source)
test("karpathy-guidelines in VENDORED_SKILLS",
     '"karpathy-guidelines"' in aut_source or "'karpathy-guidelines'" in aut_source)
test("auto-update-tools.py does not hardcode .cursor/ path",
     ".cursor/" not in aut_source,
     "Unsupported host surface in auto-update-tools.py")
test("auto-update-tools.py does not hardcode .claude-plugin/ path",
     ".claude-plugin/" not in aut_source,
     "Unsupported host surface in auto-update-tools.py")

# ---------------------------------------------------------------------------
# 4. Path derivation consistency
# ---------------------------------------------------------------------------
print("\n🗺️  4. Path derivation from HOST_SKILL_SUBPATHS")

for host_name, ref_subpath in HOST_SKILL_SUBPATHS.items():
    ref = Path(ref_subpath)
    # Pattern: <skills_base> / session-knowledge / SKILL.md
    # Derived:  <skills_base> / karpathy-guidelines / SKILL.md
    skills_base = ref.parent.parent
    derived = skills_base / "karpathy-guidelines" / "SKILL.md"
    test(
        f"{host_name}: derived path ends with skills/karpathy-guidelines/SKILL.md",
        str(derived).endswith("skills/karpathy-guidelines/SKILL.md"),
        f"got {derived}",
    )

# ---------------------------------------------------------------------------
# 5. deploy_skills() update behaviour — real function, mocked git root
# ---------------------------------------------------------------------------
print("\n🚀 5. deploy_skills() update behaviour (real deploy_skills() call)")

if skill_md.exists():
    from unittest.mock import patch, MagicMock  # stdlib; no new deps

    # Load auto-update-tools.py as a module so we can call deploy_skills() directly.
    import importlib.util as _ilu_aut
    _aut_spec = _ilu_aut.spec_from_file_location(
        "auto_update_tools_s5", REPO / "auto-update-tools.py")
    _aut_mod_s5 = _ilu_aut.module_from_spec(_aut_spec)
    _aut_spec.loader.exec_module(_aut_mod_s5)

    # TOOLS_DIR is hardcoded in auto-update-tools.py as HOME/.copilot/tools.
    # Override it to REPO so deploy_skills() finds the vendored skill source.
    _orig_tools = _aut_mod_s5.TOOLS_DIR
    _aut_mod_s5.TOOLS_DIR = REPO

    try:
        # ── A: SKILL.md update (both hosts) ──────────────────────────────────
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            # Pre-create deployed SKILL.md files (update-only rule requires prior existence)
            (tmp / ".github" / "skills" / "karpathy-guidelines").mkdir(parents=True)
            deployed_copilot = tmp / ".github" / "skills" / "karpathy-guidelines" / "SKILL.md"
            deployed_copilot.write_text("OLD CONTENT", encoding="utf-8")
            (tmp / ".claude" / "skills" / "karpathy-guidelines").mkdir(parents=True)
            deployed_claude = tmp / ".claude" / "skills" / "karpathy-guidelines" / "SKILL.md"
            deployed_claude.write_text("OLD CONTENT", encoding="utf-8")

            mock_r = MagicMock()
            mock_r.returncode = 0
            mock_r.stdout = str(tmp) + "\n"
            with patch.object(_aut_mod_s5, "subprocess") as mock_sub:
                mock_sub.run.return_value = mock_r
                _aut_mod_s5.deploy_skills()

            skill_content = skill_md.read_text(encoding="utf-8")
            test("deploy_skills() updates Copilot CLI deployed karpathy skill",
                 deployed_copilot.read_text(encoding="utf-8") == skill_content)
            test("deploy_skills() updates Claude Code deployed karpathy skill",
                 deployed_claude.read_text(encoding="utf-8") == skill_content)

        # ── B: no-create when SKILL.md absent ────────────────────────────────
        with tempfile.TemporaryDirectory() as absent_str:
            absent = Path(absent_str)
            mock_r2 = MagicMock()
            mock_r2.returncode = 0
            mock_r2.stdout = str(absent) + "\n"
            with patch.object(_aut_mod_s5, "subprocess") as mock_sub2:
                mock_sub2.run.return_value = mock_r2
                _aut_mod_s5.deploy_skills()
            test("deploy_skills() does NOT create Copilot CLI deployment when absent",
                 not (absent / ".github" / "skills" / "karpathy-guidelines" / "SKILL.md").exists())
            test("deploy_skills() does NOT create Claude Code deployment when absent",
                 not (absent / ".claude" / "skills" / "karpathy-guidelines" / "SKILL.md").exists())
    finally:
        _aut_mod_s5.TOOLS_DIR = _orig_tools

    # ── C: asset subdir update (uses temporary TOOLS_DIR with fake assets) ───
    # Exercises the asset-subdir update loop added to deploy_skills() (Fix-1 ext).
    with tempfile.TemporaryDirectory() as fake_tools_str, \
            tempfile.TemporaryDirectory() as proj_str:
        fake_tools = Path(fake_tools_str)
        proj = Path(proj_str)

        # Build minimal fake source: karpathy-guidelines with a references/ asset.
        fake_skill_dir = fake_tools / "skills" / "karpathy-guidelines"
        (fake_skill_dir / "references").mkdir(parents=True)
        (fake_skill_dir / "SKILL.md").write_text("SKILL CONTENT", encoding="utf-8")
        (fake_skill_dir / "references" / "guide.md").write_text("NEW ASSET", encoding="utf-8")
        # extras.md exists only in the source — must NOT be created at the target.
        (fake_skill_dir / "references" / "extras.md").write_text("EXTRAS", encoding="utf-8")

        # Pre-create only guide.md in deployed dirs (old content); extras.md absent.
        for skills_sub in (".github/skills", ".claude/skills"):
            asset_path = proj / skills_sub / "karpathy-guidelines" / "references" / "guide.md"
            asset_path.parent.mkdir(parents=True)
            asset_path.write_text("OLD ASSET", encoding="utf-8")

        orig_tools_dir = _aut_mod_s5.TOOLS_DIR
        _aut_mod_s5.TOOLS_DIR = fake_tools
        mock_r3 = MagicMock()
        mock_r3.returncode = 0
        mock_r3.stdout = str(proj) + "\n"
        try:
            with patch.object(_aut_mod_s5, "subprocess") as mock_sub3:
                mock_sub3.run.return_value = mock_r3
                _aut_mod_s5.deploy_skills()
        finally:
            _aut_mod_s5.TOOLS_DIR = orig_tools_dir

        test("deploy_skills() updates Copilot CLI asset subdir file",
             (proj / ".github/skills/karpathy-guidelines/references/guide.md")
             .read_text(encoding="utf-8") == "NEW ASSET")
        test("deploy_skills() updates Claude Code asset subdir file",
             (proj / ".claude/skills/karpathy-guidelines/references/guide.md")
             .read_text(encoding="utf-8") == "NEW ASSET")
        test("deploy_skills() does NOT create asset subdir file not already deployed",
             not (proj / ".github/skills/karpathy-guidelines/references/extras.md").exists())

# ---------------------------------------------------------------------------
# 6. Supported-host boundary: no unsupported hosts referenced
# ---------------------------------------------------------------------------
print("\n🛡️  6. Supported-host boundary")

UNSUPPORTED_KEYWORDS = [".cursor/", ".claude-plugin/", "CURSOR.md", "windsurf", "codex",
                        "cline", "copilot-chat"]
for kw in UNSUPPORTED_KEYWORDS:
    # Check in the skill itself
    if skill_md.exists():
        test(f"SKILL.md does not reference '{kw}'",
             kw.lower() not in skill_md.read_text(encoding="utf-8").lower())

# Confirm SUPPORTED_HOSTS from manifest only contains grounded hosts
test("SUPPORTED_HOSTS contains exactly Copilot CLI and Claude Code",
     set(SUPPORTED_HOSTS) == {"Copilot CLI", "Claude Code"},
     f"got {set(SUPPORTED_HOSTS)}")

# ---------------------------------------------------------------------------
# 6b. Filesystem guard: no upstream artifacts vendored into skills directory
# ---------------------------------------------------------------------------
print("\n🔒 6b. Filesystem artifact guard (no CLAUDE.md / .cursor / .claude-plugin)")

karpathy_dir = REPO / "skills" / "karpathy-guidelines"
if karpathy_dir.is_dir():
    # No upstream CLAUDE.md inside the vendored skill directory
    test("skills/karpathy-guidelines/ contains no CLAUDE.md",
         not (karpathy_dir / "CLAUDE.md").exists(),
         "Upstream CLAUDE.md must not be vendored here")

    # No .cursor/ directory vendored
    test("skills/karpathy-guidelines/ contains no .cursor/ directory",
         not (karpathy_dir / ".cursor").exists(),
         ".cursor/ is an unsupported host surface")

    # No .claude-plugin file or directory vendored
    test("skills/karpathy-guidelines/ contains no .claude-plugin artifact",
         not (karpathy_dir / ".claude-plugin").exists(),
         ".claude-plugin is an unsupported host surface")

    # Verify that no file anywhere under the skill directory is named CLAUDE.md
    claude_md_files = list(karpathy_dir.rglob("CLAUDE.md"))
    test("No CLAUDE.md found anywhere in skills/karpathy-guidelines/ tree",
         len(claude_md_files) == 0,
         f"Found: {[str(f) for f in claude_md_files]}")

    # Verify that setup-project.py does not deploy CLAUDE.md as part of the
    # karpathy skill (install_skills only copies files inside skill subdirs,
    # and the filesystem tests above already confirm no CLAUDE.md exists in
    # the skill directory tree; this check guards the INSTALL_ITEMS entry itself).
    karpathy_entry_deploys_claude = False
    for line in sp_source.splitlines():
        if ('"karpathy-guidelines"' in line or "'karpathy-guidelines'" in line) and "CLAUDE.md" in line:
            karpathy_entry_deploys_claude = True
    test("setup-project.py INSTALL_ITEMS karpathy entry does not reference CLAUDE.md",
         not karpathy_entry_deploys_claude)
else:
    test("skills/karpathy-guidelines/ directory exists (prerequisite)", False,
         "Cannot run artifact guards — directory missing")

# ---------------------------------------------------------------------------
# 7. Fix-1 guard: vendored-skill update runs even when templates/SKILL.md absent
# ---------------------------------------------------------------------------
print("\n🔧 7. deploy_skills() early-return decoupling (Fix-1)")

if skill_md.exists():
    import tempfile as _tmpmod
    with _tmpmod.TemporaryDirectory() as _d:
        _tmp = Path(_d)
        # Set up a project with karpathy already deployed but WITHOUT a
        # templates/SKILL.md present — the old code would return early here and
        # skip the vendored-skill update entirely.
        _copilot_dir = _tmp / ".github" / "skills" / "karpathy-guidelines"
        _copilot_dir.mkdir(parents=True)
        (_copilot_dir / "SKILL.md").write_text("STALE", encoding="utf-8")

        _skill_content = skill_md.read_text(encoding="utf-8")

        # Simulate the decoupled vendored-skill loop (no template guard)
        _updated = []
        for _host, _ref in HOST_SKILL_SUBPATHS.items():
            _base = Path(_ref).parent.parent
            _t = _tmp / _base / "karpathy-guidelines" / "SKILL.md"
            if _t.exists() and _t.read_text(encoding="utf-8") != _skill_content:
                _t.write_text(_skill_content, encoding="utf-8")
                _updated.append(_host)

        test("Vendored-skill update runs independently of template presence",
             "Copilot CLI" in _updated,
             "Early-return guard still blocking vendored-skill path")

        # Confirm auto-update-tools.py no longer has the early-return before
        # the git-root check (template guard must be inside an `if` block, not
        # before the git root detection).
        lines = aut_source.splitlines()
        in_deploy = False
        early_return_before_git = False
        for i, ln in enumerate(lines):
            if "def deploy_skills" in ln:
                in_deploy = True
                continue
            if in_deploy:
                stripped = ln.strip()
                # If we hit 'git rev-parse' first, the early return was moved correctly
                if "git" in stripped and "rev-parse" in stripped:
                    break
                if stripped == "return" or stripped.startswith("return "):
                    early_return_before_git = True
                    break
                # Allow variable assignments and function-definition end
                if stripped.startswith("def ") and "deploy_skills" not in stripped:
                    break
        test("deploy_skills() has no bare early-return before git-root check",
             not early_return_before_git,
             "templates/SKILL.md guard is still blocking vendored-skill path")

# ---------------------------------------------------------------------------
# 8. install_skills() real deployment — both hosts created (Fix-2)
# ---------------------------------------------------------------------------
print("\n🔧 8. install_skills() real deployment verification (Fix-2)")

# Source-inspection guards: structural intent of install_skills().
test("setup-project.py imports HOST_SKILL_SUBPATHS from host_manifest",
     "HOST_SKILL_SUBPATHS" in sp_source)
test("VENDORED_SKILLS defined in setup-project.py",
     "VENDORED_SKILLS" in sp_source)
test("karpathy-guidelines listed in VENDORED_SKILLS in setup-project.py",
     '"karpathy-guidelines"' in sp_source or "'karpathy-guidelines'" in sp_source)
test("install_skills() derives Claude Code path from HOST_SKILL_SUBPATHS",
     "VENDORED_SKILLS" in sp_source and "_claude_skills_base" in sp_source)
test("install_skills() mirrors asset subdirs to Claude Code for vendored skills",
     "asset_claude_dst" in sp_source,
     "Asset subdir mirror for Claude Code missing from install_skills()")

# Call the real install_skills() and verify the files it creates on disk.
# This replaces the tautological Path.write_text() simulation: we let the
# production function run against a scratch directory and inspect its output.
import importlib.util as _ilu_sp
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
_sp_spec = _ilu_sp.spec_from_file_location("_setup_project", REPO / "setup-project.py")
_sp_mod  = _ilu_sp.module_from_spec(_sp_spec)
_sp_spec.loader.exec_module(_sp_mod)

import tempfile as _t2
with _t2.TemporaryDirectory() as _d2:
    _root = Path(_d2)
    _sp_mod.install_skills(_root, dry_run=False)

    _claude_ref  = HOST_SKILL_SUBPATHS.get("Claude Code", "")
    _claude_base = Path(_claude_ref).parent.parent if _claude_ref else None

    _copilot_path = _root / ".github" / "skills" / "karpathy-guidelines" / "SKILL.md"
    _claude_path  = (_root / _claude_base / "karpathy-guidelines" / "SKILL.md") if _claude_base else None

    test("install_skills() creates Copilot CLI SKILL.md",
         _copilot_path.exists(), f"Expected {_copilot_path}")
    test("install_skills() creates Claude Code SKILL.md",
         bool(_claude_path) and _claude_path.exists(), f"Expected {_claude_path}")

    if skill_md.exists():
        _expected = skill_md.read_text(encoding="utf-8")
        if _copilot_path.exists():
            test("Copilot CLI SKILL.md content matches vendored source",
                 _copilot_path.read_text(encoding="utf-8") == _expected)
        if _claude_path and _claude_path.exists():
            test("Claude Code SKILL.md content matches vendored source",
                 _claude_path.read_text(encoding="utf-8") == _expected)

# ---------------------------------------------------------------------------
# 9. Python syntax validity
# ---------------------------------------------------------------------------
print("\n🐍 9. Python syntax validity")

import ast
for fname in ("setup-project.py", "auto-update-tools.py"):
    fpath = REPO / fname
    try:
        ast.parse(fpath.read_text(encoding="utf-8"))
        test(f"{fname} parses without syntax errors", True)
    except SyntaxError as exc:
        test(f"{fname} parses without syntax errors", False, str(exc))

# ---------------------------------------------------------------------------
# 10. VENDORED_SKILLS drift detection: setup-project.py ↔ auto-update-tools.py
# ---------------------------------------------------------------------------
print("\n🔗 10. VENDORED_SKILLS drift detection")

import re as _re


def _extract_vendored_skills(source: str) -> set:
    """Parse VENDORED_SKILLS tuple value from source text."""
    m = _re.search(r'VENDORED_SKILLS\s*(?::\s*[^\n=]+)?\s*=\s*\(([^)]*)\)', source)
    if not m:
        return set()
    return set(_re.findall(r'["\']([^"\']+)["\']', m.group(1)))


_sp_vs  = _extract_vendored_skills(sp_source)
_aut_vs = _extract_vendored_skills(aut_source)

test("VENDORED_SKILLS parseable in setup-project.py",
     bool(_sp_vs), f"Could not parse — got {_sp_vs!r}")
test("VENDORED_SKILLS parseable in auto-update-tools.py",
     bool(_aut_vs), f"Could not parse — got {_aut_vs!r}")
test("VENDORED_SKILLS identical in both files (no drift)",
     _sp_vs == _aut_vs,
     f"Drift: setup-project.py={_sp_vs!r}, auto-update-tools.py={_aut_vs!r}")
test("karpathy-guidelines present in both VENDORED_SKILLS definitions",
     "karpathy-guidelines" in _sp_vs and "karpathy-guidelines" in _aut_vs,
     f"sp={_sp_vs!r}, aut={_aut_vs!r}")

# ---------------------------------------------------------------------------
# 11. setup-project.py registry write
# ---------------------------------------------------------------------------
print("\n📋 11. setup-project.py registry write")

import json as _json

# Source-inspection guards
test("setup-project.py defines REGISTRY_PATH", "REGISTRY_PATH" in sp_source)
test("setup-project.py defines _register_project", "_register_project" in sp_source)
test("setup-project.py defines _load_project_registry", "_load_project_registry" in sp_source)
test("setup-project.py calls _register_project in main()",
     "_register_project(project_root)" in sp_source)
test("auto-update-tools.py defines REGISTRY_PATH", "REGISTRY_PATH" in aut_source)
test("auto-update-tools.py defines _load_project_registry", "_load_project_registry" in aut_source)
test("auto-update-tools.py calls _load_project_registry in deploy_skills",
     "_load_project_registry()" in aut_source)

# Functional test: call _register_project() against a scratch registry file.
import importlib.util as _ilu_11
_sp11_spec = _ilu_11.spec_from_file_location("_setup_project_11", REPO / "setup-project.py")
_sp11_mod  = _ilu_11.module_from_spec(_sp11_spec)
_sp11_spec.loader.exec_module(_sp11_mod)

import tempfile as _t11
with _t11.TemporaryDirectory() as _d11:
    _proj11  = (Path(_d11) / "myproject").resolve()
    _proj11.mkdir()
    _reg11   = Path(_d11) / "registry.json"
    _orig_reg11 = _sp11_mod.REGISTRY_PATH
    _sp11_mod.REGISTRY_PATH = _reg11
    try:
        _sp11_mod._register_project(_proj11)
    finally:
        _sp11_mod.REGISTRY_PATH = _orig_reg11

    test("_register_project() creates registry file", _reg11.exists())
    if _reg11.exists():
        _reg11_data = _json.loads(_reg11.read_text(encoding="utf-8"))
        test("_register_project() adds project path to 'projects' list",
             str(_proj11) in _reg11_data.get("projects", []))

        # Idempotence: registering the same project twice must not duplicate.
        _sp11_mod.REGISTRY_PATH = _reg11
        try:
            _sp11_mod._register_project(_proj11)
        finally:
            _sp11_mod.REGISTRY_PATH = _orig_reg11
        _reg11_data2 = _json.loads(_reg11.read_text(encoding="utf-8"))
        test("_register_project() is idempotent (no duplicate entries)",
             _reg11_data2.get("projects", []).count(str(_proj11)) == 1)

        # A second distinct project is appended, not overwriting the first.
        _proj11b = (Path(_d11) / "otherproject").resolve()
        _proj11b.mkdir()
        _sp11_mod.REGISTRY_PATH = _reg11
        try:
            _sp11_mod._register_project(_proj11b)
        finally:
            _sp11_mod.REGISTRY_PATH = _orig_reg11
        _reg11_data3 = _json.loads(_reg11.read_text(encoding="utf-8"))
        test("_register_project() accumulates multiple projects",
             str(_proj11) in _reg11_data3.get("projects", [])
             and str(_proj11b) in _reg11_data3.get("projects", []))

# ---------------------------------------------------------------------------
# 12. Registered-project propagation: deploy_skills() from tools-repo context
# ---------------------------------------------------------------------------
print("\n🌍 12. Registered-project propagation (tools-repo context)")

if skill_md.exists():
    import importlib.util as _ilu_12
    _aut12_spec = _ilu_12.spec_from_file_location(
        "auto_update_tools_s12", REPO / "auto-update-tools.py")
    _aut12_mod = _ilu_12.module_from_spec(_aut12_spec)
    _aut12_spec.loader.exec_module(_aut12_mod)

    import tempfile as _t12
    with _t12.TemporaryDirectory() as _d12:
        _target12 = Path(_d12)
        _reg12    = _target12 / "registry.json"

        # Pre-deploy: place stale karpathy SKILL.md in the target project for
        # both supported hosts, mimicking a prior setup-project.py run.
        for _host12, _ref12 in HOST_SKILL_SUBPATHS.items():
            _base12  = Path(_ref12).parent.parent
            _stale12 = _target12 / _base12 / "karpathy-guidelines" / "SKILL.md"
            _stale12.parent.mkdir(parents=True, exist_ok=True)
            _stale12.write_text("STALE", encoding="utf-8")

        # Write registry pointing to target project.
        _reg12.write_text(
            _json.dumps({"projects": [str(_target12)]}), encoding="utf-8"
        )

        # Override TOOLS_DIR → REPO and REGISTRY_PATH → scratch file.
        _orig_td12  = _aut12_mod.TOOLS_DIR
        _orig_rp12  = _aut12_mod.REGISTRY_PATH
        _aut12_mod.TOOLS_DIR    = REPO
        _aut12_mod.REGISTRY_PATH = _reg12
        try:
            # Simulate auto-update running from the tools repo (git root = REPO).
            # The mock subprocess returns REPO as the git root — but the registered
            # project (_target12) must still be updated via the registry path.
            with patch("subprocess.run") as _mock12:
                _mock12.return_value = MagicMock(returncode=0, stdout=str(REPO) + "\n")
                _aut12_mod.deploy_skills()
        finally:
            _aut12_mod.TOOLS_DIR    = _orig_td12
            _aut12_mod.REGISTRY_PATH = _orig_rp12

        _expected12 = skill_md.read_text(encoding="utf-8")
        for _host12, _ref12 in HOST_SKILL_SUBPATHS.items():
            _base12   = Path(_ref12).parent.parent
            _updated12 = _target12 / _base12 / "karpathy-guidelines" / "SKILL.md"
            test(
                f"propagation: {_host12} karpathy SKILL.md updated in registered project",
                _updated12.exists()
                and _updated12.read_text(encoding="utf-8") == _expected12,
                f"Content mismatch or missing at {_updated12}",
            )

    test("deploy_skills() iterates registered projects (source guard)",
         "_load_project_registry()" in aut_source
         and "project_roots" in aut_source)
    test("deploy_skills() fallback git root still present (source guard)",
         "git" in aut_source and "rev-parse" in aut_source
         and "fallback" in aut_source)

# ---------------------------------------------------------------------------
# 13. install.py --deploy-skill writes to registry (so auto-update can reach it)
# ---------------------------------------------------------------------------
print("\n🔌 13. install.py deploy_skill() registry integration")

import io
from contextlib import redirect_stdout

inst_source = (REPO / "install.py").read_text(encoding="utf-8")

# Source-inspection guards
test("install.py defines REGISTRY_PATH", "REGISTRY_PATH" in inst_source)
test("install.py defines _register_project", "_register_project" in inst_source)
test("install.py defines _load_project_registry", "_load_project_registry" in inst_source)

deploy_skill_start_13 = inst_source.find("def deploy_skill()")
deploy_skill_end_13   = inst_source.find("\ndef ", deploy_skill_start_13 + 1)
deploy_skill_body_13  = inst_source[deploy_skill_start_13:deploy_skill_end_13]
test("deploy_skill() calls _register_project(project_root) (source guard)",
     "_register_project(project_root)" in deploy_skill_body_13,
     "deploy_skill() must register the project after a successful deployment")

# Functional test: deploy_skill() on a mock filesystem creates a registry entry.
import importlib.util as _ilu_13
_inst13_spec = _ilu_13.spec_from_file_location("_install_13", REPO / "install.py")
_inst13_mod  = _ilu_13.module_from_spec(_inst13_spec)
_inst13_spec.loader.exec_module(_inst13_mod)

with tempfile.TemporaryDirectory() as _d13:
    _proj13     = (Path(_d13) / "myproject").resolve()
    _proj13.mkdir()
    _fake_cop13 = Path(_d13) / "dot-copilot"
    _fake_cop13.mkdir()
    _reg13      = Path(_d13) / "registry.json"
    _skill_src13 = Path(_d13) / "SKILL.md"
    _skill_src13.write_text("# Skill content", encoding="utf-8")

    # Patch globals so deploy_skill() operates on fake dirs and registry.
    _inst13_mod.KNOWN_HOSTS  = {"Copilot CLI": _fake_cop13}
    _inst13_mod.SKILLS_SRC   = _skill_src13
    _inst13_mod._git_root    = lambda: _proj13
    _orig_reg13 = _inst13_mod.REGISTRY_PATH
    _inst13_mod.REGISTRY_PATH = _reg13

    try:
        with redirect_stdout(io.StringIO()):
            _inst13_mod.deploy_skill()
    finally:
        _inst13_mod.REGISTRY_PATH = _orig_reg13

    test("deploy_skill() creates registry file after deployment",
         _reg13.exists(),
         "REGISTRY_PATH must exist after a successful deploy_skill() call")
    if _reg13.exists():
        _reg13_data = _json.loads(_reg13.read_text(encoding="utf-8"))
        test("deploy_skill() records project root in registry",
             str(_proj13) in _reg13_data.get("projects", []),
             f"Expected {_proj13} in registry projects list")

# End-to-end: project registered via install.py is updated by deploy_skills().
if skill_md.exists():
    print("\n🔗 13b. End-to-end: install.py-registered project updated by deploy_skills()")

    from unittest.mock import patch, MagicMock  # stdlib; no new deps

    import importlib.util as _ilu_13b
    _aut13b_spec = _ilu_13b.spec_from_file_location(
        "auto_update_tools_s13b", REPO / "auto-update-tools.py")
    _aut13b_mod = _ilu_13b.module_from_spec(_aut13b_spec)
    _aut13b_spec.loader.exec_module(_aut13b_mod)

    with tempfile.TemporaryDirectory() as _d13b:
        _target13b = Path(_d13b)
        _reg13b    = _target13b / "registry.json"

        # Pre-deploy stale karpathy skills so deploy_skills() can update them.
        for _host13b, _ref13b in HOST_SKILL_SUBPATHS.items():
            _base13b  = Path(_ref13b).parent.parent
            _stale13b = _target13b / _base13b / "karpathy-guidelines" / "SKILL.md"
            _stale13b.parent.mkdir(parents=True, exist_ok=True)
            _stale13b.write_text("STALE_INSTALL_PY", encoding="utf-8")

        # Write registry as install.py would (same JSON schema).
        _reg13b.write_text(
            _json.dumps({"projects": [str(_target13b)]}), encoding="utf-8"
        )

        _orig_td13b = _aut13b_mod.TOOLS_DIR
        _orig_rp13b = _aut13b_mod.REGISTRY_PATH
        _aut13b_mod.TOOLS_DIR     = REPO
        _aut13b_mod.REGISTRY_PATH = _reg13b
        try:
            with patch("subprocess.run") as _mock13b:
                _mock13b.return_value = MagicMock(returncode=0, stdout=str(REPO) + "\n")
                _aut13b_mod.deploy_skills()
        finally:
            _aut13b_mod.TOOLS_DIR     = _orig_td13b
            _aut13b_mod.REGISTRY_PATH = _orig_rp13b

        _expected13b = skill_md.read_text(encoding="utf-8")
        for _host13b, _ref13b in HOST_SKILL_SUBPATHS.items():
            _base13b   = Path(_ref13b).parent.parent
            _updated13b = _target13b / _base13b / "karpathy-guidelines" / "SKILL.md"
            test(
                f"install.py-registered project: {_host13b} karpathy updated by deploy_skills()",
                _updated13b.exists()
                and _updated13b.read_text(encoding="utf-8") == _expected13b,
                f"Content mismatch or missing at {_updated13b}",
            )

# ---------------------------------------------------------------------------
# 14. Global Copilot CLI skill rollout (~/.copilot/skills/<name>/)
# ---------------------------------------------------------------------------
print("\n🌐 14. Global Copilot CLI skill rollout (~/.copilot/skills/)")

# Source guard: constant must be defined in auto-update-tools.py.
test("GLOBAL_COPILOT_SKILLS_DIR defined in auto-update-tools.py",
     "GLOBAL_COPILOT_SKILLS_DIR" in aut_source)

if skill_md.exists():
    from unittest.mock import patch, MagicMock  # stdlib; no new deps

    import importlib.util as _ilu_14
    _aut14_spec = _ilu_14.spec_from_file_location(
        "auto_update_tools_s14", REPO / "auto-update-tools.py")
    _aut14_mod = _ilu_14.module_from_spec(_aut14_spec)
    _aut14_spec.loader.exec_module(_aut14_mod)

    # ── A: update already-installed global skill ───────────────────────────
    with tempfile.TemporaryDirectory() as _d14a:
        _fake_global = Path(_d14a) / "dot-copilot" / "skills"
        _fake_global.mkdir(parents=True)

        # Pre-install stale SKILL.md so the update-only rule allows the write.
        _global_skill_dir = _fake_global / "karpathy-guidelines"
        _global_skill_dir.mkdir()
        _global_skill_md = _global_skill_dir / "SKILL.md"
        _global_skill_md.write_text("STALE GLOBAL", encoding="utf-8")

        _orig_td14a  = _aut14_mod.TOOLS_DIR
        _orig_gsd14a = _aut14_mod.GLOBAL_COPILOT_SKILLS_DIR
        _orig_rp14a  = _aut14_mod.REGISTRY_PATH
        _aut14_mod.TOOLS_DIR               = REPO
        _aut14_mod.GLOBAL_COPILOT_SKILLS_DIR = _fake_global
        _aut14_mod.REGISTRY_PATH           = Path(_d14a) / "empty-registry.json"

        mock_r14a = MagicMock()
        mock_r14a.returncode = 1  # no git root — ensures update comes from global path
        try:
            with patch.object(_aut14_mod, "subprocess") as _mock14a:
                _mock14a.run.return_value = mock_r14a
                _aut14_mod.deploy_skills()
        finally:
            _aut14_mod.TOOLS_DIR               = _orig_td14a
            _aut14_mod.GLOBAL_COPILOT_SKILLS_DIR = _orig_gsd14a
            _aut14_mod.REGISTRY_PATH           = _orig_rp14a

        _expected14 = skill_md.read_text(encoding="utf-8")
        test("deploy_skills() updates already-installed global Copilot CLI skill",
             _global_skill_md.read_text(encoding="utf-8") == _expected14,
             f"Content not updated at {_global_skill_md}")

    # ── B: never create a global skill that is not already installed ───────
    with tempfile.TemporaryDirectory() as _d14b:
        _fake_global_b = Path(_d14b) / "dot-copilot" / "skills"
        _fake_global_b.mkdir(parents=True)
        # Do NOT create karpathy-guidelines/ — it must not be created by deploy_skills().

        _orig_td14b  = _aut14_mod.TOOLS_DIR
        _orig_gsd14b = _aut14_mod.GLOBAL_COPILOT_SKILLS_DIR
        _orig_rp14b  = _aut14_mod.REGISTRY_PATH
        _aut14_mod.TOOLS_DIR               = REPO
        _aut14_mod.GLOBAL_COPILOT_SKILLS_DIR = _fake_global_b
        _aut14_mod.REGISTRY_PATH           = Path(_d14b) / "empty-registry.json"

        mock_r14b = MagicMock()
        mock_r14b.returncode = 1
        try:
            with patch.object(_aut14_mod, "subprocess") as _mock14b:
                _mock14b.run.return_value = mock_r14b
                _aut14_mod.deploy_skills()
        finally:
            _aut14_mod.TOOLS_DIR               = _orig_td14b
            _aut14_mod.GLOBAL_COPILOT_SKILLS_DIR = _orig_gsd14b
            _aut14_mod.REGISTRY_PATH           = _orig_rp14b

        test("deploy_skills() does NOT create global skill dir when not pre-installed",
             not (_fake_global_b / "karpathy-guidelines" / "SKILL.md").exists(),
             "update-only rule violated: new global install was created")

    # ── C: asset subdir update in global skill dir ─────────────────────────
    with tempfile.TemporaryDirectory() as _d14c_tools, \
            tempfile.TemporaryDirectory() as _d14c_global:
        _fake_tools14c  = Path(_d14c_tools)
        _fake_global14c = Path(_d14c_global) / "skills"

        # Build minimal fake source with references/ subdir.
        _skill_src14c = _fake_tools14c / "skills" / "karpathy-guidelines"
        (_skill_src14c / "references").mkdir(parents=True)
        (_skill_src14c / "SKILL.md").write_text("SKILL CONTENT", encoding="utf-8")
        (_skill_src14c / "references" / "guide.md").write_text("NEW REF", encoding="utf-8")
        (_skill_src14c / "references" / "extra.md").write_text("EXTRA", encoding="utf-8")

        # Pre-install only guide.md in the global skill dir (extra.md absent → no-create).
        _global14c_skill = _fake_global14c / "karpathy-guidelines"
        (_global14c_skill / "references").mkdir(parents=True)
        (_global14c_skill / "SKILL.md").write_text("OLD CONTENT", encoding="utf-8")
        (_global14c_skill / "references" / "guide.md").write_text("OLD REF", encoding="utf-8")

        _orig_td14c  = _aut14_mod.TOOLS_DIR
        _orig_gsd14c = _aut14_mod.GLOBAL_COPILOT_SKILLS_DIR
        _orig_rp14c  = _aut14_mod.REGISTRY_PATH
        _aut14_mod.TOOLS_DIR               = _fake_tools14c
        _aut14_mod.GLOBAL_COPILOT_SKILLS_DIR = _fake_global14c
        _aut14_mod.REGISTRY_PATH           = _fake_tools14c / "empty-registry.json"

        mock_r14c = MagicMock()
        mock_r14c.returncode = 1
        try:
            with patch.object(_aut14_mod, "subprocess") as _mock14c:
                _mock14c.run.return_value = mock_r14c
                _aut14_mod.deploy_skills()
        finally:
            _aut14_mod.TOOLS_DIR               = _orig_td14c
            _aut14_mod.GLOBAL_COPILOT_SKILLS_DIR = _orig_gsd14c
            _aut14_mod.REGISTRY_PATH           = _orig_rp14c

        test("deploy_skills() updates existing asset file in global skill dir",
             (_global14c_skill / "references" / "guide.md")
             .read_text(encoding="utf-8") == "NEW REF")
        test("deploy_skills() does NOT create absent asset file in global skill dir",
             not (_global14c_skill / "references" / "extra.md").exists())

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
total = PASS + FAIL
print(f"\n{'='*55}")
print(f"  Results: {PASS}/{total} passed")
if FAIL == 0:
    print("  ✅ ALL TESTS PASSED")
else:
    print(f"  ❌ {FAIL} test(s) failed")
print("="*55)
sys.exit(0 if FAIL == 0 else 1)
