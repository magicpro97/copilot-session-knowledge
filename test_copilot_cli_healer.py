#!/usr/bin/env python3
"""test_copilot_cli_healer.py — Tests for copilot-cli-healer.py

Covers the 9 cases from CONTEXT.md §6.
Run: python test_copilot_cli_healer.py
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

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
        print(f"  \u2705 {name}")
    else:
        FAIL += 1
        print(f"  \u274c {name}" + (f" \u2014 {detail}" if detail else ""))


def _load_healer(pkg_dir: Path):
    """Load copilot-cli-healer as a module with pkg_dir set via env var."""
    os.environ["COPILOT_HEALER_PKG_DIR"] = str(pkg_dir)
    spec = importlib.util.spec_from_file_location(
        "copilot_cli_healer",
        str(REPO / "copilot-cli-healer.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_pkg(base: Path):
    """Create pkg/universal/ and pkg/tmp/ under base; return (pkg, universal, tmp)."""
    pkg = base / "pkg"
    universal = pkg / "universal"
    tmp = pkg / "tmp"
    universal.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return pkg, universal, tmp


# ─── Test 1: Clean pkg dir ──────────────────────────────────────────────────
print("\n\U0001f50d Test 1: Clean pkg dir — no issues, heal is no-op")

with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    pkg, universal, tmp = _make_pkg(base)
    # Add a healthy, non-empty version dir
    v = universal / "1.0.35"
    v.mkdir()
    (v / "copilot").write_bytes(b"x" * 2048)

    healer = _load_healer(pkg)
    issues = healer.check(pkg)
    test("No issues on clean dir", len(issues) == 0, str(issues))

    actions = healer.heal(pkg)
    test("Heal is no-op on clean dir", len(actions) == 0, str(actions))

    # Verify healthy dir still present after heal
    test("Healthy version dir intact after heal", v.exists())


# ─── Test 2: Stale .replaced-* dir ─────────────────────────────────────────
print("\n\U0001f50d Test 2: Stale .replaced-* dir — detect + remove")

with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    pkg, universal, tmp = _make_pkg(base)
    stale = universal / ".replaced-1.0.35-12345-1714000000"
    stale.mkdir()
    (stale / "junk").write_bytes(b"x" * 100)

    healer = _load_healer(pkg)
    issues = healer.check(pkg)
    test(
        "Detects .replaced-* as replaced_dir issue",
        len(issues) == 1 and issues[0].kind == "replaced_dir",
        str(issues),
    )

    healer.heal(pkg)
    test("Heal removes .replaced-* dir", not stale.exists())


# ─── Test 3: Stale tmp/ entry ───────────────────────────────────────────────
print("\n\U0001f50d Test 3: Stale tmp/ entry — remove contents, preserve tmp/")

with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    pkg, universal, tmp = _make_pkg(base)
    partial = tmp / "1.0.35-12345-1714000000"
    partial.mkdir()
    (partial / "download.part").write_bytes(b"x" * 500)

    healer = _load_healer(pkg)
    issues = healer.check(pkg)
    test(
        "Detects tmp entry as tmp_entry issue",
        any(i.kind == "tmp_entry" for i in issues),
        str(issues),
    )

    healer.heal(pkg)
    test("Heal removes partial download subdir", not partial.exists())
    test("Heal preserves tmp/ dir itself", tmp.exists())


# ─── Test 4: Empty dummy version dir ────────────────────────────────────────
print("\n\U0001f50d Test 4: Empty dummy version dir — detect + remove")

with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    pkg, universal, tmp = _make_pkg(base)
    dummy = universal / "1.0.35"
    dummy.mkdir()  # empty

    healer = _load_healer(pkg)
    issues = healer.check(pkg)
    test(
        "Detects empty version dir as empty_dummy issue",
        any(i.kind == "empty_dummy" for i in issues),
        str(issues),
    )

    healer.heal(pkg)
    test("Heal removes empty dummy dir", not dummy.exists())


# ─── Test 5: Non-empty healthy version dir — untouched ─────────────────────
print("\n\U0001f50d Test 5: Non-empty healthy version dir — left alone")

with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    pkg, universal, tmp = _make_pkg(base)
    healthy = universal / "1.0.35"
    healthy.mkdir()
    binary = healthy / "copilot"
    binary.write_bytes(b"x" * 5000)

    healer = _load_healer(pkg)
    issues = healer.check(pkg)
    test("No issues for healthy non-empty version dir", len(issues) == 0, str(issues))

    healer.heal(pkg)
    test("Heal leaves healthy version dir in place", healthy.exists())
    test("Heal leaves binary inside intact", binary.exists())


# ─── Test 6: Missing pkg dir entirely ───────────────────────────────────────
print("\n\U0001f50d Test 6: Missing pkg dir — exit 0, no-op")

with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    nonexistent_pkg = base / "pkg"  # intentionally not created

    healer = _load_healer(nonexistent_pkg)
    issues = healer.check(nonexistent_pkg)
    test("No issues when pkg dir absent", len(issues) == 0, str(issues))

    actions = healer.heal(nonexistent_pkg)
    test("Heal no-op when pkg dir absent", len(actions) == 0, str(actions))


# ─── Test 7: Concurrent heal lock ───────────────────────────────────────────
print("\n\U0001f50d Test 7: Concurrent heal — second attempt blocked")

with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    pkg, universal, tmp = _make_pkg(base)

    healer = _load_healer(pkg)

    # Simulate an existing lock (another process holding it)
    lock_path = pkg / ".healer.lock"
    lock_path.write_text("99999", encoding="utf-8")

    fd = healer._acquire_lock()
    test("Second heal attempt blocked by existing lock", fd is None)

    # Remove the fake lock and verify acquisition works after
    lock_path.unlink()
    fd2 = healer._acquire_lock()
    test("Lock acquirable after stale lock removed", fd2 is not None)
    if fd2 is not None:
        healer._release_lock(fd2)
    test("Lock file removed after release", not lock_path.exists())


# ─── Test 8: Windows path handling via COPILOT_HEALER_PKG_DIR env var ───────
print("\n\U0001f50d Test 8: Windows path handling via env var override")

with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    pkg, universal, tmp = _make_pkg(base)
    stale = universal / ".replaced-1.0.35-999-1714"
    stale.mkdir()

    os.environ["COPILOT_HEALER_PKG_DIR"] = str(pkg)
    healer = _load_healer(pkg)

    pkg_from_env = healer._get_pkg_dir()
    test(
        "_get_pkg_dir() reads COPILOT_HEALER_PKG_DIR env var",
        pkg_from_env == pkg,
        f"got {pkg_from_env!r} expected {pkg!r}",
    )

    # check() with no explicit arg should use env var path
    issues = healer.check()
    test("check() with no arg uses env var pkg_dir", len(issues) >= 1, str(issues))

    # heal() with no explicit arg should also use env var
    healer.heal()
    test("heal() with no arg uses env var pkg_dir", not stale.exists())


# ─── Test 9: Dry-run — prints but does not delete ───────────────────────────
print("\n\U0001f50d Test 9: Dry-run mode — actions reported but nothing deleted")

with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    pkg, universal, tmp = _make_pkg(base)
    stale = universal / ".replaced-1.0.35-dry-0"
    stale.mkdir()
    (stale / "x").write_bytes(b"data")

    healer = _load_healer(pkg)
    actions = healer.heal(pkg, dry_run=True)
    test("Dry-run returns non-empty actions list", len(actions) > 0, str(actions))
    test("Dry-run does NOT remove stale .replaced-* dir", stale.exists())

    # Verify stale dir still detectable after dry-run
    issues_after = healer.check(pkg)
    test("Stale state still detected after dry-run", len(issues_after) > 0)


# ─── Summary ─────────────────────────────────────────────────────────────────
print(f"\n{'=' * 50}")
total = PASS + FAIL
print(f"  {PASS}/{total} tests passed", end="")
if FAIL:
    print(f" ({FAIL} failed)")
    sys.exit(1)
else:
    print(" \u2014 all good! \u2705")
    sys.exit(0)
