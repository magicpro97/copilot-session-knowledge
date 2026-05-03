#!/usr/bin/env python3
"""
benchmark.py — Commit-keyed benchmark ledger for copilot-session-knowledge.

Records retro + health snapshots keyed by git commit SHA, then compares
them over time to show measurable evolution.

Usage:
    python3 benchmark.py record [--db PATH] [--commit SHA] [--mode local|repo]
    python3 benchmark.py compare [--db PATH] [--commits SHA SHA] [--limit N]
    python3 benchmark.py list [--db PATH] [--limit N] [--json]

Signals captured (all read-only):
    - retro.py  → retro_score, subscores, score_confidence
    - knowledge-health.py → health.score (when available)
    - git HEAD  → commit_sha, commit_msg

Standalone script: no imports from other tools at module level.
Stdlib-only; optional dynamic loading of retro.py / knowledge-health.py.
"""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
SESSION_STATE = Path.home() / ".copilot" / "session-state"
DEFAULT_DB = SESSION_STATE / "knowledge.db"

_VALID_MODES = ("local", "repo")


# ── DB helpers ───────────────────────────────────────────────────────────────


def _open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_table(conn)
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS benchmark_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_sha TEXT NOT NULL DEFAULT '',
            commit_msg TEXT DEFAULT '',
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            mode TEXT NOT NULL DEFAULT 'repo',
            retro_score REAL DEFAULT 0.0,
            score_confidence TEXT DEFAULT '',
            subscores_json TEXT NOT NULL DEFAULT '{}',
            health_score REAL DEFAULT NULL,
            health_json TEXT DEFAULT NULL,
            extra_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_bsnap_commit ON benchmark_snapshots(commit_sha);
        CREATE INDEX IF NOT EXISTS idx_bsnap_recorded ON benchmark_snapshots(recorded_at);
    """)
    conn.commit()


# ── Git helpers ──────────────────────────────────────────────────────────────


def _git_head_sha(repo_root: Path = SCRIPT_DIR) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out
    except Exception:
        return ""


def _git_head_msg(repo_root: Path = SCRIPT_DIR) -> str:
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%s", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out[:200]
    except Exception:
        return ""


# ── Module loaders ───────────────────────────────────────────────────────────


def _load_module(name: str, filename: str):
    path = SCRIPT_DIR / filename
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


# ── Signal collectors ────────────────────────────────────────────────────────


def _collect_retro(mode: str, db_path: Path) -> dict:
    """Run retro signal collection and scoring (read-only)."""
    retro = _load_module("retro", "retro.py")
    if retro is None:
        return {"available": False}
    try:
        if mode == "repo":
            knowledge = {"available": False, "total": 0}
            skills = {"available": False}
            hooks = {"available": False}
            git = retro.collect_git_signals(repo_root=SCRIPT_DIR)
            payload = retro.compute_retro(
                knowledge=knowledge,
                skills=skills,
                hooks=hooks,
                git=git,
                mode="repo",
                db_path=None,
            )
        else:
            knowledge = retro.collect_knowledge_signals() if db_path.exists() else {"available": False, "total": 0}
            skills = retro.collect_skill_signals()
            hooks = retro.collect_audit_signals()
            git = retro.collect_git_signals(repo_root=SCRIPT_DIR)
            payload = retro.compute_retro(
                knowledge=knowledge,
                skills=skills,
                hooks=hooks,
                git=git,
                mode="local",
                db_path=db_path if db_path.exists() else None,
            )
        payload["available"] = True
        return payload
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _collect_health(db_path: Path) -> dict:
    """Run knowledge health collection (read-only)."""
    kh = _load_module("knowledge_health", "knowledge-health.py")
    if kh is None:
        return {"available": False}
    if not db_path.exists():
        return {"available": False}
    orig_db_path = getattr(kh, "DB_PATH", None)
    try:
        orig_argv = sys.argv[:]
        sys.argv = [sys.argv[0], str(db_path)]
        try:
            if orig_db_path is not None:
                kh.DB_PATH = db_path
            h = kh.compute_health()
        finally:
            sys.argv = orig_argv
            if orig_db_path is not None:
                kh.DB_PATH = orig_db_path
        h["available"] = True
        return h
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return {"available": False, "error": f"SystemExit({code})"}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


# ── Record ───────────────────────────────────────────────────────────────────


def cmd_record(db_path: Path, commit_sha: str, mode: str) -> int:
    """Capture current signals and store a snapshot row."""
    if not commit_sha:
        commit_sha = _git_head_sha()
    commit_msg = _git_head_msg()

    retro_data = _collect_retro(mode, db_path)
    health_data = _collect_health(db_path)

    retro_score = float(retro_data.get("retro_score", 0.0)) if retro_data.get("available") else 0.0
    score_confidence = retro_data.get("score_confidence", "") if retro_data.get("available") else ""
    subscores = retro_data.get("subscores", {}) if retro_data.get("available") else {}
    health_score_val: "float | None" = None
    health_stored: "dict | None" = None
    if health_data.get("available"):
        raw = health_data.get("score")
        if raw is not None:
            health_score_val = float(raw)
        health_stored = {k: v for k, v in health_data.items() if k not in ("available",)}

    extra: dict = {}
    if retro_data.get("available"):
        extra["grade"] = retro_data.get("grade", "")
        extra["distortion_flags"] = retro_data.get("distortion_flags", [])
        extra["available_sections"] = retro_data.get("available_sections", [])

    conn = _open_db(db_path)
    conn.execute(
        """
        INSERT INTO benchmark_snapshots
          (commit_sha, commit_msg, mode, retro_score, score_confidence,
           subscores_json, health_score, health_json, extra_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            commit_sha,
            commit_msg,
            mode,
            retro_score,
            score_confidence,
            json.dumps(subscores),
            health_score_val,
            json.dumps(health_stored) if health_stored is not None else None,
            json.dumps(extra),
        ),
    )
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    print(f"benchmark: recorded snapshot #{row_id}")
    print(f"  commit:  {commit_sha or '(none)'} {commit_msg[:60]}")
    print(f"  mode:    {mode}")
    print(f"  retro:   {retro_score}/100  confidence={score_confidence or 'n/a'}")
    if health_score_val is not None:
        print(f"  health:  {health_score_val}/100")
    if subscores:
        parts = "  ".join(f"{k}={v}" for k, v in subscores.items())
        print(f"  subscores: {parts}")
    return 0


# ── List ─────────────────────────────────────────────────────────────────────


def cmd_list(db_path: Path, limit: int, as_json: bool) -> int:
    """List recent benchmark snapshots."""
    if not db_path.exists():
        if as_json:
            print("[]")
        print("benchmark: no DB found; run 'record' first.", file=sys.stderr)
        return 0
    conn = _open_db(db_path)
    rows = conn.execute(
        """
        SELECT id, commit_sha, commit_msg, recorded_at, mode,
               retro_score, score_confidence, subscores_json, health_score
        FROM benchmark_snapshots
        ORDER BY recorded_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    if as_json:
        out = []
        for r in rows:
            d = dict(r)
            d["subscores"] = json.loads(d.pop("subscores_json", "{}"))
            d["retro_gap"] = _gap_to_target(d.get("retro_score"))
            d["health_gap"] = _gap_to_target(d.get("health_score"))
            out.append(d)
        print(json.dumps(out, indent=2))
        return 0

    if not rows:
        print("benchmark: no snapshots yet.")
        return 0

    print(f"{'ID':>4}  {'Commit':12}  {'Recorded':19}  {'Mode':6}  {'Retro':>5}  {'Health':>6}  {'Gap':>5}  Msg")
    print("-" * 96)
    for r in rows:
        health_str = f"{r['health_score']:.1f}" if r["health_score"] is not None else "  n/a"
        gap_val = _gap_to_target(r["retro_score"])
        gap_str = f"{gap_val:.1f}" if gap_val is not None else "  n/a"
        msg = (r["commit_msg"] or "")[:40]
        print(
            f"{r['id']:>4}  {(r['commit_sha'] or '?')[:12]:12}  "
            f"{r['recorded_at'][:19]:19}  {r['mode']:6}  "
            f"{r['retro_score']:>5.1f}  {health_str:>6}  {gap_str:>5}  {msg}"
        )
    return 0


# ── Compare ──────────────────────────────────────────────────────────────────


def _delta_str(a: "float | None", b: "float | None") -> str:
    if a is None or b is None:
        return "n/a"
    d = b - a
    arrow = "▲" if d > 0 else ("▼" if d < 0 else "─")
    return f"{arrow}{abs(d):.1f}"


def _gap_to_target(score: "float | None", target: float = 100.0) -> "float | None":
    """Return distance from score to target (None if score is None)."""
    if score is None:
        return None
    return round(max(0.0, target - score), 1)


def _gap_progress_str(gap_a: "float | None", gap_b: "float | None") -> str:
    """Describe gap change: ▲ closer = improvement, ▼ farther = regression."""
    if gap_a is None or gap_b is None:
        return "n/a"
    closed = round(gap_a - gap_b, 1)
    if closed > 0:
        return f"▲{closed:.1f} closer"
    elif closed < 0:
        return f"▼{abs(closed):.1f} farther"
    return "─ unchanged"


def cmd_compare(db_path: Path, commits: "list[str]", limit: int) -> int:
    """Compare two snapshots (by commit SHA prefix or row ID)."""
    if not db_path.exists():
        print("benchmark: no DB found; run 'record' first.")
        return 1
    conn = _open_db(db_path)

    def _fetch_snapshot(ref: str) -> "sqlite3.Row | None":
        # Try numeric row id first
        if ref.isdigit():
            return conn.execute(
                "SELECT * FROM benchmark_snapshots WHERE id = ?", (int(ref),)
            ).fetchone()
        # Then commit sha prefix
        return conn.execute(
            "SELECT * FROM benchmark_snapshots WHERE commit_sha LIKE ? ORDER BY recorded_at DESC LIMIT 1",
            (ref + "%",),
        ).fetchone()

    if commits and len(commits) == 2:
        snap_a = _fetch_snapshot(commits[0])
        snap_b = _fetch_snapshot(commits[1])
    else:
        # Default: compare last two snapshots
        rows = conn.execute(
            "SELECT * FROM benchmark_snapshots ORDER BY recorded_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        if len(rows) < 2:
            print("benchmark: need at least 2 snapshots to compare (got {}).".format(len(rows)))
            conn.close()
            return 1
        snap_b, snap_a = rows[0], rows[1]  # b=newer, a=older

    conn.close()

    if snap_a is None or snap_b is None:
        print("benchmark: one or both snapshots not found.")
        return 1

    def _row_label(r: sqlite3.Row) -> str:
        return f"#{r['id']} {(r['commit_sha'] or '?')[:12]} @ {r['recorded_at'][:19]}"

    print(f"\nbenchmark compare")
    print(f"  baseline : {_row_label(snap_a)}")
    print(f"  current  : {_row_label(snap_b)}")
    print()

    # retro score
    delta_retro = _delta_str(snap_a["retro_score"], snap_b["retro_score"])
    print(f"  retro_score : {snap_a['retro_score']:.1f}  →  {snap_b['retro_score']:.1f}  ({delta_retro})")

    # health score
    delta_health = _delta_str(snap_a["health_score"], snap_b["health_score"])
    h_a = f"{snap_a['health_score']:.1f}" if snap_a["health_score"] is not None else "n/a"
    h_b = f"{snap_b['health_score']:.1f}" if snap_b["health_score"] is not None else "n/a"
    print(f"  health_score: {h_a:>5}  →  {h_b:>5}  ({delta_health})")

    # gap to 100
    gap_a_retro = _gap_to_target(snap_a["retro_score"])
    gap_b_retro = _gap_to_target(snap_b["retro_score"])
    gap_a_health = _gap_to_target(snap_a["health_score"])
    gap_b_health = _gap_to_target(snap_b["health_score"])

    def _fmt_g(g: "float | None") -> str:
        return f"{g:.1f}" if g is not None else "n/a"

    gp_retro = _gap_progress_str(gap_a_retro, gap_b_retro)
    gp_health = _gap_progress_str(gap_a_health, gap_b_health)

    print()
    print("  gap to 100:")
    print(f"    retro_score : {_fmt_g(gap_a_retro):>5}  →  {_fmt_g(gap_b_retro):>5}  ({gp_retro})")
    print(f"    health_score: {_fmt_g(gap_a_health):>5}  →  {_fmt_g(gap_b_health):>5}  ({gp_health})")

    # subscores
    ss_a = json.loads(snap_a["subscores_json"] or "{}")
    ss_b = json.loads(snap_b["subscores_json"] or "{}")
    all_keys = sorted(set(ss_a) | set(ss_b))
    if all_keys:
        print()
        print("  subscores:")
        for k in all_keys:
            va = ss_a.get(k)
            vb = ss_b.get(k)
            d = _delta_str(va, vb)
            va_s = f"{va:.1f}" if va is not None else "n/a"
            vb_s = f"{vb:.1f}" if vb is not None else "n/a"
            print(f"    {k:12} {va_s:>5}  →  {vb_s:>5}  ({d})")

    print()
    print("  proof summary:")
    print(f"    retro  : {snap_a['retro_score']:.1f} → {snap_b['retro_score']:.1f}  gap {_fmt_g(gap_a_retro)} → {_fmt_g(gap_b_retro)}  {gp_retro}")
    print(f"    health : {h_a} → {h_b}  gap {_fmt_g(gap_a_health)} → {_fmt_g(gap_b_health)}  {gp_health}")
    print()
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parse_args(argv: list) -> dict:
    args = {
        "cmd": None,
        "db": DEFAULT_DB,
        "commit": "",
        "mode": "repo",
        "commits": [],
        "limit": 10,
        "json": False,
    }
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("record", "compare", "list"):
            args["cmd"] = a
        elif a == "--db" and i + 1 < len(argv):
            i += 1
            args["db"] = Path(argv[i])
        elif a == "--commit" and i + 1 < len(argv):
            i += 1
            args["commit"] = argv[i]
        elif a == "--mode" and i + 1 < len(argv):
            i += 1
            args["mode"] = argv[i]
        elif a == "--limit" and i + 1 < len(argv):
            i += 1
            args["limit"] = int(argv[i])
        elif a == "--commits" and i + 2 < len(argv):
            args["commits"] = [argv[i + 1], argv[i + 2]]
            i += 2
        elif a == "--json":
            args["json"] = True
        i += 1
    return args


def main(argv: "list | None" = None) -> int:
    if argv is None:
        argv = sys.argv
    args = _parse_args(argv)

    if args["cmd"] is None:
        print(
            "Usage: benchmark.py <record|compare|list> [--db PATH] [--commit SHA] "
            "[--mode local|repo] [--limit N] [--json] [--commits SHA SHA]"
        )
        return 1

    db_path = Path(args["db"])
    mode = args["mode"]
    if mode not in _VALID_MODES:
        print(f"benchmark: invalid mode '{mode}'; use one of {_VALID_MODES}", file=sys.stderr)
        return 1

    if args["cmd"] == "record":
        return cmd_record(db_path, args["commit"], mode)
    elif args["cmd"] == "list":
        return cmd_list(db_path, args["limit"], args["json"])
    elif args["cmd"] == "compare":
        return cmd_compare(db_path, args["commits"], args["limit"])
    else:
        print(f"benchmark: unknown command '{args['cmd']}'", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
