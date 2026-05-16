#!/usr/bin/env python3
"""test_skill_catalog.py — Tests for skill-catalog.py.

Covers:
  - _parse_skill_yml: basic field parsing
  - _validate_skill_yml: valid + invalid manifests
  - cmd_catalog: lists official skills, marks installed ones
  - cmd_add (official): copies files, writes registry entry with digest
  - cmd_add (already installed): no-op unless --force
  - cmd_add (community, non-https): rejected
  - cmd_add (community, ZIP with Zip Slip): rejected
  - cmd_remove: removes files + registry, checks digest
  - cmd_remove (digest mismatch without --force): rejected
  - cmd_remove (digest mismatch with --force): succeeds
  - sk skill namespace: routes to skill-catalog.py subcommands

Run: python3 tests/test_skill_catalog.py
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).parent.parent
CATALOG_SCRIPT = TOOLS_DIR / "skill-catalog.py"
SK_SCRIPT = TOOLS_DIR / "sk.py"

PASS = 0
FAIL = 0


def _test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def _load_module(path: Path, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    saved_argv = sys.argv[:]
    sys.argv = [str(path)]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = saved_argv
    return mod


sc = _load_module(CATALOG_SCRIPT, "_skill_catalog")
sk = _load_module(SK_SCRIPT, "_sk")

# ---------------------------------------------------------------------------
# Scratch directory
# ---------------------------------------------------------------------------
SCRATCH = TOOLS_DIR / ".test-scratch" / "skill-catalog-tests"
SCRATCH.mkdir(parents=True, exist_ok=True)


def _make_fake_project(base: Path) -> Path:
    """Create a minimal fake project root with a .git dir."""
    proj = base / "fake-project"
    if proj.exists():
        shutil.rmtree(proj)
    proj.mkdir(parents=True)
    (proj / ".git").mkdir()
    return proj


def _make_official_skills_dir(base: Path) -> Path:
    """Create a minimal skills/ directory with two fake official skills."""
    skills = base / "skills"
    for name in ("alpha-skill", "beta-skill"):
        sd = skills / name
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Fake skill {name}\n---\n# {name}\n",
            encoding="utf-8",
        )
        (sd / "skill.yml").write_text(
            f'schema_version: 1\nprovides:\n  commands:\n    - {name}\nrequires:\n  sk_version: ">=1.0.0"\n',
            encoding="utf-8",
        )
    refs = skills / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "skill-standards.md").write_text("not a skill", encoding="utf-8")
    return skills


# ── 1. _parse_skill_yml ────────────────────────────────────────────────────

print("\n📄 _parse_skill_yml")

yml_text = """\
schema_version: 1
provides:
  commands:
    - my-command
    - other-cmd
requires:
  sk_version: ">=1.0.0"
hooks:
  before_plan: do something
  after_implement: check output
"""

parsed = sc._parse_skill_yml(yml_text)
_test("schema_version parsed as int", parsed.get("schema_version") == 1)
_test("provides.commands parsed", parsed.get("provides", {}).get("commands") == ["my-command", "other-cmd"])
_test("requires.sk_version parsed", parsed.get("requires", {}).get("sk_version") == ">=1.0.0")
_test("hooks.before_plan parsed", parsed.get("hooks", {}).get("before_plan") == "do something")
_test("hooks.after_implement parsed", parsed.get("hooks", {}).get("after_implement") == "check output")

# ── 2. _validate_skill_yml ────────────────────────────────────────────────

print("\n✅ _validate_skill_yml")

valid_data = {
    "schema_version": 1,
    "provides": {"commands": ["my-cmd"]},
    "requires": {"sk_version": ">=1.0.0"},
}
errs = sc._validate_skill_yml(valid_data)
_test("valid manifest → no errors", errs == [], f"errors: {errs}")

bad_schema = dict(valid_data)
bad_schema["schema_version"] = 2
errs = sc._validate_skill_yml(bad_schema)
_test("unsupported schema_version → error", any("schema_version" in e for e in errs))

missing_schema = {k: v for k, v in valid_data.items() if k != "schema_version"}
errs = sc._validate_skill_yml(missing_schema)
_test("missing schema_version → error", any("schema_version" in e for e in errs))

missing_commands = {"schema_version": 1, "provides": {}, "requires": {"sk_version": ">=1.0.0"}}
errs = sc._validate_skill_yml(missing_commands)
_test("missing provides.commands → error", any("commands" in e for e in errs))

empty_commands = {"schema_version": 1, "provides": {"commands": []}, "requires": {"sk_version": ">=1.0.0"}}
errs = sc._validate_skill_yml(empty_commands)
_test("empty provides.commands → error", any("commands" in e for e in errs))

missing_sk_version = {"schema_version": 1, "provides": {"commands": ["x"]}, "requires": {}}
errs = sc._validate_skill_yml(missing_sk_version)
_test("missing requires.sk_version → error", any("sk_version" in e for e in errs))

# ── 3. cmd_catalog ────────────────────────────────────────────────────────

print("\n📋 cmd_catalog")

_catalog_scratch = SCRATCH / "catalog_test"
_catalog_scratch.mkdir(parents=True, exist_ok=True)
_fake_skills = _make_official_skills_dir(_catalog_scratch)
_fake_project = _make_fake_project(_catalog_scratch)

# Monkey-patch the official skills dir
original_skills_dir = sc._OFFICIAL_SKILLS_DIR
sc._OFFICIAL_SKILLS_DIR = _fake_skills

try:
    # JSON output
    import argparse

    ns = argparse.Namespace(json=True)
    with patch("builtins.print") as mock_print:
        rc = sc.cmd_catalog(ns)
    captured = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
    _test("catalog --json exits 0", rc == 0)
    _test("catalog --json contains 'alpha-skill'", "alpha-skill" in captured)
    _test("catalog --json contains 'beta-skill'", "beta-skill" in captured)
    _test("catalog --json omits non-skill references dir", "references" not in captured)

    # Text output
    ns_text = argparse.Namespace(json=False)
    # Patch _find_project_root to avoid depending on real CWD
    with patch.object(sc, "_find_project_root", return_value=_fake_project):
        with patch("builtins.print") as mock_print:
            rc2 = sc.cmd_catalog(ns_text)
    captured2 = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
    _test("catalog text exits 0", rc2 == 0)
    _test("catalog text lists skills", "alpha-skill" in captured2 or "beta-skill" in captured2)
    _test("catalog text omits non-skill references dir", "references" not in captured2)

finally:
    sc._OFFICIAL_SKILLS_DIR = original_skills_dir

# ── 4. cmd_add (official) ─────────────────────────────────────────────────

print("\n📦 cmd_add (official)")

_add_scratch = SCRATCH / "add_test"
_add_scratch.mkdir(parents=True, exist_ok=True)
_add_skills = _make_official_skills_dir(_add_scratch)
_add_project = _make_fake_project(_add_scratch)

sc._OFFICIAL_SKILLS_DIR = _add_skills

try:
    import argparse

    ns_add = argparse.Namespace(name="alpha-skill", **{"from": None}, force=False)
    with patch.object(sc, "_find_project_root", return_value=_add_project):
        with patch("builtins.print"):
            rc = sc.cmd_add(ns_add)

    _test("add official skill exits 0", rc == 0)
    dest = _add_project / ".copilot" / "skills" / "alpha-skill"
    _test("skill directory created", dest.exists())
    _test("SKILL.md present", (dest / "SKILL.md").exists())
    _test("skill.yml present", (dest / "skill.yml").exists())

    reg = sc._load_registry(_add_project)
    _test("registry entry created", "alpha-skill" in reg.get("skills", {}))
    entry = reg["skills"]["alpha-skill"]
    _test("registry has digest", bool(entry.get("digest")))
    _test("registry source is official", entry.get("source") == "official")

    # Idempotent (no-op if already installed)
    with patch.object(sc, "_find_project_root", return_value=_add_project):
        with patch("builtins.print") as mock_print2:
            rc2 = sc.cmd_add(ns_add)
    out2 = " ".join(str(c) for call in mock_print2.call_args_list for c in call[0])
    _test("re-add no-op exits 0", rc2 == 0)
    _test("re-add prints already installed", "already installed" in out2.lower())

    # Unknown skill
    ns_bad = argparse.Namespace(name="nonexistent-skill", **{"from": None}, force=False)
    with patch.object(sc, "_find_project_root", return_value=_add_project):
        rc_bad = sc.cmd_add(ns_bad)
    _test("add unknown skill exits 1", rc_bad == 1)

finally:
    sc._OFFICIAL_SKILLS_DIR = original_skills_dir

# ── 5. cmd_add (community – non-HTTPS rejected) ────────────────────────────

print("\n🔒 cmd_add community (HTTPS enforcement)")

_https_scratch = SCRATCH / "https_test"
_https_scratch.mkdir(parents=True, exist_ok=True)
_https_project = _make_fake_project(_https_scratch)

ns_http = argparse.Namespace(name=None, **{"from": "http://example.com/skill.zip"}, force=False)
with patch.object(sc, "_find_project_root", return_value=_https_project):
    rc_http = sc.cmd_add(ns_http)
_test("HTTP (non-HTTPS) community URL rejected with rc=1", rc_http == 1)

ns_ftp = argparse.Namespace(name=None, **{"from": "ftp://example.com/skill.zip"}, force=False)
with patch.object(sc, "_find_project_root", return_value=_https_project):
    rc_ftp = sc.cmd_add(ns_ftp)
_test("FTP community URL rejected with rc=1", rc_ftp == 1)

# ── 6. cmd_add (community – valid ZIP) ────────────────────────────────────

print("\n📦 cmd_add community (valid ZIP)")

_comm_scratch = SCRATCH / "comm_test"
_comm_scratch.mkdir(parents=True, exist_ok=True)
_comm_project = _make_fake_project(_comm_scratch)


def _make_valid_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "my-community-skill/skill.yml",
            'schema_version: 1\nprovides:\n  commands:\n    - comm-cmd\nrequires:\n  sk_version: ">=1.0.0"\n',
        )
        zf.writestr("my-community-skill/SKILL.md", "# Community Skill\n")
    return buf.getvalue()


valid_zip_bytes = _make_valid_zip()

ns_comm = argparse.Namespace(name=None, **{"from": "https://example.com/comm.zip"}, force=False)
with patch.object(sc, "_find_project_root", return_value=_comm_project):
    with patch.object(sc, "_download_https_zip", return_value=valid_zip_bytes):
        with patch("builtins.print"):
            rc_comm = sc.cmd_add(ns_comm)

_test("community add from valid ZIP exits 0", rc_comm == 0)
dest_comm = _comm_project / ".copilot" / "skills" / "comm-cmd"
_test("community skill dir created", dest_comm.exists())
reg_comm = sc._load_registry(_comm_project)
_test("community registry entry created", "comm-cmd" in reg_comm.get("skills", {}))
entry_comm = reg_comm["skills"]["comm-cmd"]
_test("community entry source is 'community'", entry_comm.get("source") == "community")
_test("community entry has source_url", bool(entry_comm.get("source_url")))
_test("community entry has digest", bool(entry_comm.get("digest")))

# ── 7. Zip Slip protection ─────────────────────────────────────────────────

print("\n🛡️  Zip Slip protection")

_slip_scratch = SCRATCH / "zipslip_test"
_slip_scratch.mkdir(parents=True, exist_ok=True)
_slip_project = _make_fake_project(_slip_scratch)


def _make_slip_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "evil-skill/skill.yml",
            'schema_version: 1\nprovides:\n  commands:\n    - evil\nrequires:\n  sk_version: ">=1.0.0"\n',
        )
        zf.writestr("evil-skill/../../escaped.txt", "pwned")
    return buf.getvalue()


slip_zip_bytes = _make_slip_zip()

ns_slip = argparse.Namespace(name=None, **{"from": "https://evil.example.com/slip.zip"}, force=False)
with patch.object(sc, "_find_project_root", return_value=_slip_project):
    with patch.object(sc, "_download_https_zip", return_value=slip_zip_bytes):
        rc_slip = sc.cmd_add(ns_slip)
_test("Zip Slip ZIP rejected (rc=1)", rc_slip == 1)

# ── 8. cmd_add (community – missing SKILL.md) ───────────────────────────────

print("\n📄 cmd_add community (missing SKILL.md)")

_missing_skill_md_scratch = SCRATCH / "missing_skill_md_test"
_missing_skill_md_scratch.mkdir(parents=True, exist_ok=True)
_missing_skill_md_project = _make_fake_project(_missing_skill_md_scratch)


def _make_missing_skill_md_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "broken-skill/skill.yml",
            'schema_version: 1\nprovides:\n  commands:\n    - broken\nrequires:\n  sk_version: ">=1.0.0"\n',
        )
    return buf.getvalue()


missing_skill_md_zip = _make_missing_skill_md_zip()

ns_missing_skill_md = argparse.Namespace(
    name=None,
    **{"from": "https://example.com/broken.zip"},
    force=False,
)
with patch.object(sc, "_find_project_root", return_value=_missing_skill_md_project):
    with patch.object(sc, "_download_https_zip", return_value=missing_skill_md_zip):
        rc_missing_skill_md = sc.cmd_add(ns_missing_skill_md)
_test("community ZIP without SKILL.md rejected (rc=1)", rc_missing_skill_md == 1)

# ── 9. cmd_remove ─────────────────────────────────────────────────────────

print("\n🗑️  cmd_remove")

_rem_scratch = SCRATCH / "remove_test"
_rem_scratch.mkdir(parents=True, exist_ok=True)
_rem_skills = _make_official_skills_dir(_rem_scratch)
_rem_project = _make_fake_project(_rem_scratch)

sc._OFFICIAL_SKILLS_DIR = _rem_skills

try:
    # Install first
    ns_install = argparse.Namespace(name="beta-skill", **{"from": None}, force=False)
    with patch.object(sc, "_find_project_root", return_value=_rem_project):
        with patch("builtins.print"):
            sc.cmd_add(ns_install)

    # Verify installed
    dest_rem = _rem_project / ".copilot" / "skills" / "beta-skill"
    _test("setup: skill was installed", dest_rem.exists())

    # Remove
    ns_rem = argparse.Namespace(name="beta-skill", force=False)
    with patch.object(sc, "_find_project_root", return_value=_rem_project):
        with patch("builtins.print"):
            rc_rem = sc.cmd_remove(ns_rem)

    _test("remove exits 0", rc_rem == 0)
    _test("skill directory removed", not dest_rem.exists())
    reg_rem = sc._load_registry(_rem_project)
    _test("registry entry removed", "beta-skill" not in reg_rem.get("skills", {}))

    # Remove non-installed
    ns_missing = argparse.Namespace(name="nonexistent", force=False)
    with patch.object(sc, "_find_project_root", return_value=_rem_project):
        rc_missing = sc.cmd_remove(ns_missing)
    _test("remove non-installed skill exits 1", rc_missing == 1)

finally:
    sc._OFFICIAL_SKILLS_DIR = original_skills_dir

# ── 10. cmd_remove (digest mismatch) ──────────────────────────────────────

print("\n🔐 cmd_remove digest mismatch")

_digest_scratch = SCRATCH / "digest_test"
_digest_scratch.mkdir(parents=True, exist_ok=True)
_digest_skills = _make_official_skills_dir(_digest_scratch)
_digest_project = _make_fake_project(_digest_scratch)

sc._OFFICIAL_SKILLS_DIR = _digest_skills

try:
    # Install alpha-skill
    ns_install2 = argparse.Namespace(name="alpha-skill", **{"from": None}, force=False)
    with patch.object(sc, "_find_project_root", return_value=_digest_project):
        with patch("builtins.print"):
            sc.cmd_add(ns_install2)

    # Tamper with installed file
    skill_dest = _digest_project / ".copilot" / "skills" / "alpha-skill"
    (skill_dest / "tampered.txt").write_text("modified", encoding="utf-8")

    # Remove without --force → should fail
    ns_rem2 = argparse.Namespace(name="alpha-skill", force=False)
    with patch.object(sc, "_find_project_root", return_value=_digest_project):
        rc_mismatch = sc.cmd_remove(ns_rem2)
    _test("digest mismatch without --force → rc=1", rc_mismatch == 1)

    # Remove with --force → should succeed
    ns_rem2_force = argparse.Namespace(name="alpha-skill", force=True)
    with patch.object(sc, "_find_project_root", return_value=_digest_project):
        with patch("builtins.print"):
            rc_force = sc.cmd_remove(ns_rem2_force)
    _test("digest mismatch with --force → rc=0", rc_force == 0)

finally:
    sc._OFFICIAL_SKILLS_DIR = original_skills_dir

# ── 11. sk skill namespace routing ────────────────────────────────────────

print("\n🔀 sk skill namespace routing")


def _mock_run(script, extra_args):
    """Track calls without actually running subprocess."""
    return (script, extra_args)


with patch.object(sk, "_run", side_effect=lambda s, a: 0):
    # sk skill catalog
    rc_sk_cat = sk.main(["skill", "catalog"])
    _test("sk skill catalog routes (rc=0)", rc_sk_cat == 0)

    # sk skill add agent-creator
    rc_sk_add = sk.main(["skill", "add", "agent-creator"])
    _test("sk skill add routes (rc=0)", rc_sk_add == 0)

    # sk skill remove agent-creator
    rc_sk_rem = sk.main(["skill", "remove", "agent-creator"])
    _test("sk skill remove routes (rc=0)", rc_sk_rem == 0)

# sk skill unknown subcommand
import io as _io

captured_err = _io.StringIO()
with patch.object(sys, "stderr", captured_err):
    rc_sk_unk = sk.main(["skill", "unknown-sub"])
_test("sk skill unknown subcommand → rc=2", rc_sk_unk == 2)

# sk skill (no subcommand) → help
with patch("builtins.print"):
    rc_sk_help = sk.main(["skill"])
_test("sk skill (no subcommand) → rc=0 (help)", rc_sk_help == 0)

# Ensure old direct commands still work
with patch.object(sk, "_run", return_value=0):
    rc_suggest = sk.main(["skill-suggest"])
    _test("sk skill-suggest direct command still works", rc_suggest == 0)

    rc_patch = sk.main(["skill-patch"])
    _test("sk skill-patch direct command still works", rc_patch == 0)

    rc_curator = sk.main(["skill-curator"])
    _test("sk skill-curator direct command still works", rc_curator == 0)

# ── 12. _sha256_dir stability ─────────────────────────────────────────────

print("\n🔑 _sha256_dir")

_sha_dir = SCRATCH / "sha256_test"
_sha_dir.mkdir(parents=True, exist_ok=True)
(_sha_dir / "a.txt").write_text("hello", encoding="utf-8")
(_sha_dir / "b.txt").write_text("world", encoding="utf-8")

d1 = sc._sha256_dir(_sha_dir)
d2 = sc._sha256_dir(_sha_dir)
_test("_sha256_dir is stable (same result twice)", d1 == d2)

# Modifying a file changes the digest
(_sha_dir / "a.txt").write_text("modified", encoding="utf-8")
d3 = sc._sha256_dir(_sha_dir)
_test("_sha256_dir changes after file modification", d1 != d3)

# ── Cleanup ────────────────────────────────────────────────────────────────

try:
    shutil.rmtree(SCRATCH)
except Exception:
    pass

# ── Summary ────────────────────────────────────────────────────────────────

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    sys.exit(1)
