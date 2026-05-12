#!/usr/bin/env python3
"""
tests/test_dream_score.py — Tests for dream.py scoring (issue #159).

Covers:
  - Score calculation for all 6 signals
  - Weighted sum computation
  - Gate logic (min_score, min_recall, min_queries)
  - --dry-run: scores not persisted to DB
  - Normal run: scores persisted to entry_dream_scores
  - --json output structure
  - --top N slicing
  - Weight validation (negative weight → exit 2)
  - Edge cases: empty DB, entry with no recall stats, entry with no tags

Run: python3 tests/test_dream_score.py
"""

import importlib.util
import json
import math
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).parent.parent
DREAM_PY = TOOLS_DIR / "dream.py"

PASS = 0
FAIL = 0


def test(name: str, passed: bool, detail: str = "") -> None:
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f": {detail}" if detail else ""))


def _load_dream():
    spec = importlib.util.spec_from_file_location("dream_mod", str(DREAM_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dream = _load_dream()

_DB_SCHEMA = """
    CREATE TABLE IF NOT EXISTS knowledge_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'pattern',
        title TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '',
        confidence REAL DEFAULT 1.0,
        occurrence_count INTEGER DEFAULT 1,
        stable_id TEXT,
        tags TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS entry_recall_stats (
        entry_id INTEGER PRIMARY KEY,
        recall_count INTEGER NOT NULL DEFAULT 0,
        recall_days INTEGER NOT NULL DEFAULT 0,
        unique_queries INTEGER NOT NULL DEFAULT 0,
        first_recalled_at TEXT,
        last_recalled_at TEXT
    );
    CREATE TABLE IF NOT EXISTS entry_concept_tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL,
        tag TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'auto',
        tagged_at TEXT DEFAULT (datetime('now')),
        UNIQUE(entry_id, tag)
    );
"""


def _make_db(entries=None, recall_stats=None, tags=None) -> tuple[str, sqlite3.Connection]:
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="dream_test_")
    os.close(fd)
    conn = sqlite3.connect(db_path)
    conn.executescript(_DB_SCHEMA)
    conn.commit()
    if entries:
        for e in entries:
            conn.execute(
                "INSERT INTO knowledge_entries "
                "(session_id, category, title, content, confidence, occurrence_count) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    e.get("session_id", "s1"),
                    e.get("category", "pattern"),
                    e.get("title", "T"),
                    e.get("content", "C"),
                    e.get("confidence", 1.0),
                    e.get("occurrence_count", 1),
                ),
            )
    if recall_stats:
        for r in recall_stats:
            conn.execute(
                "INSERT INTO entry_recall_stats "
                "(entry_id, recall_count, recall_days, unique_queries, last_recalled_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    r["entry_id"],
                    r.get("recall_count", 0),
                    r.get("recall_days", 0),
                    r.get("unique_queries", 0),
                    r.get("last_recalled_at"),
                ),
            )
    if tags:
        for t in tags:
            conn.execute(
                "INSERT OR IGNORE INTO entry_concept_tags (entry_id, tag) VALUES (?, ?)",
                (t["entry_id"], t["tag"]),
            )
    conn.commit()
    return db_path, conn


def _cleanup(db_path: str, conn: sqlite3.Connection) -> None:
    conn.close()
    try:
        os.unlink(db_path)
    except OSError:
        pass


# ─── 1. Unit tests: compute_score signals ───────────────────────────────────

print("\n🧮 compute_score — signal calculations")

W = dream.DEFAULT_WEIGHTS


def _score(**kwargs):
    defaults = dict(
        recall_count=0,
        recall_days=0,
        unique_queries=0,
        last_recalled_at=None,
        tag_count=0,
        confidence=1.0,
        occurrence_count=1,
        weights=W,
    )
    defaults.update(kwargs)
    return dream.compute_score(**defaults)


def _test_signal_zero_recall():
    s = _score(recall_count=0, unique_queries=0, tag_count=0)
    # frequency=0, relevance=0, diversity=0, conceptual=0
    # recency: last_recalled_at=None → days=9999 → ~0
    # consolidation: confidence=1 * normalize(1,20)=0.05
    test("signal_frequency=0 when recall_count=0", s["signal_frequency"] == 0.0, f"got {s['signal_frequency']}")
    test("signal_relevance=0 when unique_queries=0", s["signal_relevance"] == 0.0, f"got {s['signal_relevance']}")
    test("signal_diversity=0 when tag_count=0", s["signal_diversity"] == 0.0, f"got {s['signal_diversity']}")
    test("signal_conceptual=0 when tag_count=0", s["signal_conceptual"] == 0.0, f"got {s['signal_conceptual']}")
    test("signal_recency≈0 when last_recalled_at=None", s["signal_recency"] < 0.01, f"got {s['signal_recency']}")


_test_signal_zero_recall()


def _test_signal_max_caps():
    s = _score(recall_count=1000, unique_queries=1000, tag_count=100)
    test("signal_frequency=1.0 at recall_count cap", s["signal_frequency"] == 1.0, f"got {s['signal_frequency']}")
    test("signal_relevance=1.0 at unique_queries cap", s["signal_relevance"] == 1.0, f"got {s['signal_relevance']}")
    test("signal_diversity=1.0 at tag_count cap", s["signal_diversity"] == 1.0, f"got {s['signal_diversity']}")
    test("signal_conceptual=1.0 when tag_count>0", s["signal_conceptual"] == 1.0, f"got {s['signal_conceptual']}")


_test_signal_max_caps()


def _test_signal_recency():
    # Recency at half-life should be ~0.5
    from datetime import datetime, timedelta, timezone

    halflife_ago = (datetime.now(timezone.utc) - timedelta(days=dream._RECENCY_HALFLIFE_DAYS)).isoformat()
    s = _score(last_recalled_at=halflife_ago)
    test("signal_recency≈0.5 at half-life", abs(s["signal_recency"] - 0.5) < 0.02, f"got {s['signal_recency']}")

    # Very recent recall → ~1.0
    recent = datetime.now(timezone.utc).isoformat()
    s2 = _score(last_recalled_at=recent)
    test("signal_recency≈1.0 for just-recalled entry", s2["signal_recency"] > 0.99, f"got {s2['signal_recency']}")


_test_signal_recency()


def _test_consolidation():
    # confidence=1.0, occurrence_count=20 → sig_con = 1.0 * 1.0 = 1.0
    s = _score(confidence=1.0, occurrence_count=20, recall_count=0, unique_queries=0)
    test(
        "signal_consolidation=1.0 at max occurrence",
        s["signal_consolidation"] == 1.0,
        f"got {s['signal_consolidation']}",
    )
    # confidence=0.5, occurrence_count=10 → sig_con = 0.5 * 0.5 = 0.25
    s2 = _score(confidence=0.5, occurrence_count=10, recall_count=0, unique_queries=0)
    test(
        "signal_consolidation=0.25 for conf=0.5, occ=10",
        abs(s2["signal_consolidation"] - 0.25) < 0.01,
        f"got {s2['signal_consolidation']}",
    )


_test_consolidation()


def _test_weighted_sum():
    # With all signals = 1.0, score = sum(weights) = 1.0 (default weights sum to 1.0)
    total_w = sum(W.values())
    from datetime import datetime, timezone

    s = _score(
        recall_count=1000,
        unique_queries=1000,
        tag_count=100,
        confidence=1.0,
        occurrence_count=20,
        last_recalled_at=datetime.now(timezone.utc).isoformat(),
    )
    test(
        "score ≈ sum(weights) when all signals=1.0",
        abs(s["score"] - total_w) < 0.02,
        f"score={s['score']}, total_w={total_w}",
    )


_test_weighted_sum()


# ─── 2. Gate logic ──────────────────────────────────────────────────────────

print("\n🔒 Gate logic")


def _test_gate():
    db_path, conn = _make_db(
        entries=[
            {"title": "Entry A", "confidence": 1.0, "occurrence_count": 5},
            {"title": "Entry B", "confidence": 0.1, "occurrence_count": 1},
        ],
        recall_stats=[
            # Entry 1: passes gate (high recall, many queries, recent)
            {"entry_id": 1, "recall_count": 10, "unique_queries": 5, "last_recalled_at": "2025-01-01T00:00:00Z"},
            # Entry 2: fails gate (low recall)
            {"entry_id": 2, "recall_count": 1, "unique_queries": 1, "last_recalled_at": None},
        ],
        tags=[
            {"entry_id": 1, "tag": "python"},
            {"entry_id": 1, "tag": "testing"},
        ],
    )
    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = dream.run_scoring(
            conn=conn,
            weights=W,
            min_score=0.0,  # permissive score gate
            min_recall=3,  # recall gate
            min_queries=2,  # queries gate
            dry_run=True,
            top_n=20,
            as_json=True,
        )
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    _cleanup(db_path, conn)

    test("gate run returns 0", rc == 0, f"got {rc}")
    data = json.loads(output)
    test("total_entries=2", data["total_entries"] == 2, str(data))
    # Entry 1 has recall_count=10>=3 AND unique_queries=5>=2 AND score>0
    # Entry 2 fails recall gate (1 < 3) and queries gate (1 < 2)
    test("gate_count=1 (only Entry A passes)", data["gate_count"] == 1, f"gate_count={data['gate_count']}")
    top = data["top"]
    passing = [r for r in top if r["passes_gate"]]
    test("Entry A passes gate", len(passing) == 1 and passing[0]["title"] == "Entry A", str(passing))


_test_gate()


# ─── 3. Persistence ─────────────────────────────────────────────────────────

print("\n💾 Persistence — scores written to entry_dream_scores")


def _test_persist():
    db_path, conn = _make_db(
        entries=[{"title": "X", "confidence": 0.9, "occurrence_count": 3}],
        recall_stats=[
            {"entry_id": 1, "recall_count": 5, "unique_queries": 3, "last_recalled_at": "2025-06-01T00:00:00Z"},
        ],
        tags=[{"entry_id": 1, "tag": "sql"}],
    )
    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = dream.run_scoring(
            conn=conn,
            weights=W,
            min_score=dream.DEFAULT_MIN_SCORE,
            min_recall=dream.DEFAULT_MIN_RECALL,
            min_queries=dream.DEFAULT_MIN_QUERIES,
            dry_run=False,
            top_n=20,
            as_json=True,
        )
    finally:
        sys.stdout = old_stdout
    test("persist run returns 0", rc == 0, f"rc={rc}")
    row = conn.execute("SELECT score, passes_gate FROM entry_dream_scores WHERE entry_id=1").fetchone()
    test("entry_dream_scores row written for entry 1", row is not None, "row is None")
    if row:
        test("score is float > 0", isinstance(row[0], float) and row[0] > 0, f"score={row[0]}")
    _cleanup(db_path, conn)


_test_persist()


def _test_dry_run_no_persist():
    db_path, conn = _make_db(
        entries=[{"title": "Y", "confidence": 1.0, "occurrence_count": 1}],
    )
    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        dream.run_scoring(
            conn=conn,
            weights=W,
            min_score=0.0,
            min_recall=0,
            min_queries=0,
            dry_run=True,
            top_n=20,
            as_json=True,
        )
    finally:
        sys.stdout = old_stdout
    row = conn.execute("SELECT COUNT(*) FROM entry_dream_scores").fetchone()
    # Table may not even exist when dry_run=True (we still create it, but no rows)
    test("dry_run: no rows persisted", row[0] == 0, f"count={row[0]}")
    _cleanup(db_path, conn)


_test_dry_run_no_persist()


# ─── 4. JSON output ─────────────────────────────────────────────────────────

print("\n📄 JSON output structure")


def _test_json_structure():
    db_path, conn = _make_db(
        entries=[
            {"title": "Alpha", "confidence": 1.0, "occurrence_count": 2},
            {"title": "Beta", "confidence": 0.5, "occurrence_count": 1},
        ],
        recall_stats=[
            {"entry_id": 1, "recall_count": 8, "unique_queries": 4, "last_recalled_at": "2025-05-01T00:00:00Z"},
        ],
    )
    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = dream.run_scoring(
            conn=conn,
            weights=W,
            min_score=0.0,
            min_recall=0,
            min_queries=0,
            dry_run=True,
            top_n=5,
            as_json=True,
        )
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    _cleanup(db_path, conn)

    test("json run returns 0", rc == 0, f"rc={rc}")
    try:
        data = json.loads(output)
        test("json output is valid JSON", True)
    except json.JSONDecodeError as e:
        test("json output is valid JSON", False, str(e))
        return

    for key in ("total_entries", "gate_count", "dry_run", "weights", "gates", "top"):
        test(f"json has '{key}' key", key in data, f"keys={list(data)}")
    test("total_entries=2", data.get("total_entries") == 2)
    if data.get("top"):
        top_item = data["top"][0]
        for sig in (
            "score",
            "signal_frequency",
            "signal_relevance",
            "signal_diversity",
            "signal_recency",
            "signal_consolidation",
            "signal_conceptual",
            "passes_gate",
            "entry_id",
            "title",
            "category",
        ):
            test(f"top item has '{sig}'", sig in top_item, f"keys={list(top_item)}")


_test_json_structure()


# ─── 5. --top N slicing ─────────────────────────────────────────────────────

print("\n🔢 --top N slicing")


def _test_top_n():
    # Create 5 entries, ask for top 3
    db_path, conn = _make_db(
        entries=[{"title": f"E{i}"} for i in range(5)],
    )
    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        dream.run_scoring(
            conn=conn,
            weights=W,
            min_score=0.0,
            min_recall=0,
            min_queries=0,
            dry_run=True,
            top_n=3,
            as_json=True,
        )
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    _cleanup(db_path, conn)
    data = json.loads(output)
    test("top_n=3 returns at most 3 items", len(data["top"]) <= 3, f"got {len(data['top'])}")
    test("total_entries=5 regardless of top_n", data["total_entries"] == 5, f"got {data['total_entries']}")


_test_top_n()


# ─── 6. CLI main() routing ──────────────────────────────────────────────────

print("\n🚦 CLI main() routing")


def _test_cli_dry_run():
    db_path, conn = _make_db(
        entries=[{"title": "CLI-test"}],
    )
    conn.close()
    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = dream.main(["--dry-run", "--json", "--db", db_path])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    try:
        os.unlink(db_path)
    except OSError:
        pass

    test("main --dry-run --json returns 0", rc == 0, f"rc={rc}")
    try:
        data = json.loads(output)
        test("--dry-run sets dry_run=True in JSON", data.get("dry_run") is True, str(data.get("dry_run")))
    except json.JSONDecodeError:
        test("CLI JSON is parseable", False, output[:200])


_test_cli_dry_run()


def _test_cli_invalid_weight():
    import io

    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        rc = dream.main(["--w-frequency", "-0.1", "--db", "nonexistent_test.db"])
    finally:
        sys.stderr = old_stderr
    test("negative weight → exit 2", rc == 2, f"rc={rc}")


_test_cli_invalid_weight()


def _test_cli_invalid_top():
    import io

    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        rc = dream.main(["--top", "0", "--db", "nonexistent_test.db"])
    finally:
        sys.stderr = old_stderr
    test("--top 0 → exit 2", rc == 2, f"rc={rc}")


_test_cli_invalid_top()


# ─── 7. Empty DB ────────────────────────────────────────────────────────────

print("\n📭 Empty DB")


def _test_empty_db():
    db_path, conn = _make_db()
    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = dream.run_scoring(
            conn=conn,
            weights=W,
            min_score=0.0,
            min_recall=0,
            min_queries=0,
            dry_run=True,
            top_n=10,
            as_json=True,
        )
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    _cleanup(db_path, conn)
    test("empty DB returns 0", rc == 0, f"rc={rc}")
    data = json.loads(output)
    test("empty DB: total_entries=0", data.get("total_entries") == 0)
    test("empty DB: gate_count=0", data.get("gate_count") == 0)


_test_empty_db()


# ─── 8. _ensure_dream_scores_table idempotency ──────────────────────────────

print("\n🔄 _ensure_dream_scores_table idempotency")


def _test_idempotent_table():
    db_path, conn = _make_db()
    try:
        dream._ensure_dream_scores_table(conn)
        dream._ensure_dream_scores_table(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        test("entry_dream_scores table created", "entry_dream_scores" in tables, str(tables))
    except Exception as e:
        test("_ensure_dream_scores_table idempotent", False, str(e))
    finally:
        _cleanup(db_path, conn)


_test_idempotent_table()


# ─── 9. fetch_entries handles missing recall stats gracefully ────────────────

print("\n🕳️ fetch_entries — missing recall stats")


def _test_fetch_no_recall():
    db_path, conn = _make_db(
        entries=[{"title": "No recall entry", "confidence": 0.8, "occurrence_count": 2}],
    )
    try:
        entries = dream.fetch_entries(conn)
        test("fetch_entries returns 1 entry", len(entries) == 1, str(entries))
        e = entries[0]
        test("recall_count=0 for entry with no recall stats", e["recall_count"] == 0, f"got {e['recall_count']}")
        test("unique_queries=0 for entry with no recall stats", e["unique_queries"] == 0, f"got {e['unique_queries']}")
        test("tag_count=0 for entry with no tags", e["tag_count"] == 0, f"got {e['tag_count']}")
    finally:
        _cleanup(db_path, conn)


_test_fetch_no_recall()


# ─── 10. Regression: naive timestamps must not zero recency signal ───────────

print("\n🕐 Regression — naive timestamp handling")


def _test_days_since_naive_timestamp():
    """_days_since() must handle timestamps written by briefing.py without timezone suffix."""
    # briefing.py writes 'YYYY-MM-DDTHH:MM:SS' — no 'Z', no '+00:00'
    naive_ts = "2025-01-01T12:00:00"
    days = dream._days_since(naive_ts)
    test("naive timestamp: days_since < 9999", days < 9999.0, f"got {days} (expected real days, not sentinel)")
    test("naive timestamp: days_since > 0", days > 0, f"got {days}")
    sig = dream._sig_recency(days)
    test("naive timestamp: recency signal > 0", sig > 0.0, f"got {sig}")

    # Timezone-aware and naive parsing of the same moment must agree (within 1 s)
    aware_ts = "2025-01-01T12:00:00+00:00"
    diff = abs(dream._days_since(naive_ts) - dream._days_since(aware_ts))
    test("naive and aware timestamps yield same days_since", diff < 0.00002, f"diff={diff} days")


_test_days_since_naive_timestamp()


def _test_recency_not_zeroed_for_naive_last_recalled():
    """compute_score must return a real recency signal for naive timestamps from briefing.py."""
    s = _score(last_recalled_at="2025-06-01T00:00:00")  # no timezone suffix
    test(
        "recency signal > 0 for naive timestamp (not zeroed)",
        s["signal_recency"] > 0.0,
        f"got {s['signal_recency']}",
    )
    test(
        "recency signal < 1.0 for past naive timestamp",
        s["signal_recency"] < 1.0,
        f"got {s['signal_recency']}",
    )


_test_recency_not_zeroed_for_naive_last_recalled()


# ─── 11. Regression: weight renormalization must cap scores at ≤ 1.0 ─────────

print("\n⚖️  Regression — weight renormalization")


def _test_weights_renormalized_via_cli():
    """When weights sum > 1.0, main() must renormalize so scores stay <= 1.0."""
    db_path, conn = _make_db(
        entries=[{"title": "R", "confidence": 1.0, "occurrence_count": 20}],
        recall_stats=[
            {
                "entry_id": 1,
                "recall_count": 100,
                "unique_queries": 50,
                "last_recalled_at": "2025-01-01T00:00:00Z",
            }
        ],
        tags=[{"entry_id": 1, "tag": "python"}],
    )
    conn.close()

    import io

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        rc = dream.main(
            [
                "--json",
                "--dry-run",
                "--db",
                db_path,
                # weights sum = 2.0 (all doubled)
                "--w-frequency",
                "0.48",
                "--w-relevance",
                "0.60",
                "--w-diversity",
                "0.30",
                "--w-recency",
                "0.30",
                "--w-consolidation",
                "0.20",
                "--w-conceptual",
                "0.12",
            ]
        )
        output = sys.stdout.getvalue()
        stderr_out = sys.stderr.getvalue()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    try:
        os.unlink(db_path)
    except OSError:
        pass

    test("overweight CLI: returns 0", rc == 0, f"rc={rc}")
    try:
        data = json.loads(output)
    except json.JSONDecodeError as e:
        test("overweight CLI: JSON parseable", False, str(e))
        return
    top = data["top"]
    test("overweight CLI: has top entry", len(top) == 1, f"len={len(top)}")
    if top:
        test(
            "overweight CLI: score <= 1.0 after renorm",
            top[0]["score"] <= 1.001,
            f"score={top[0]['score']}",
        )
    test("overweight CLI: renorm warning emitted", "renormalized" in stderr_out, f"stderr={stderr_out!r}")


_test_weights_renormalized_via_cli()


# ─── 12. Regression: low-sum weights make gate unreachable ────────────────────

print("\n\u26a0\ufe0f  Regression — low-sum weights (gate unreachable)")


def _test_low_sum_weights_warning():
    """When total weight < min_score, the gate can never be satisfied.

    main() must emit a clear stderr diagnostic instead of silently returning
    gate_count=0 with no explanation.
    """
    db_path, conn = _make_db(
        entries=[{"title": "LowWeightEntry", "confidence": 1.0, "occurrence_count": 20}],
        recall_stats=[
            {
                "entry_id": 1,
                "recall_count": 100,
                "unique_queries": 50,
                "last_recalled_at": "2025-01-01T00:00:00Z",
            }
        ],
        tags=[{"entry_id": 1, "tag": "python"}],
    )
    conn.close()

    import io

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        # weights sum = 0.10 + 0.05 = 0.15, far below default min_score=0.75
        rc = dream.main(
            [
                "--json",
                "--dry-run",
                "--db",
                db_path,
                "--w-frequency",
                "0.10",
                "--w-relevance",
                "0.05",
                "--w-diversity",
                "0.00",
                "--w-recency",
                "0.00",
                "--w-consolidation",
                "0.00",
                "--w-conceptual",
                "0.00",
            ]
        )
        stdout_out = sys.stdout.getvalue()
        stderr_out = sys.stderr.getvalue()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    try:
        os.unlink(db_path)
    except OSError:
        pass

    test("low-sum weights: returns 0 (not an error exit)", rc == 0, f"rc={rc}")
    test(
        "low-sum weights: stderr warns gate is unreachable",
        "max" in stderr_out and "min-score" in stderr_out,
        f"stderr={stderr_out!r}",
    )
    try:
        data = json.loads(stdout_out)
        test(
            "low-sum weights: gate_count=0 (gate is unreachable)",
            data.get("gate_count") == 0,
            f"gate_count={data.get('gate_count')}",
        )
        top = data.get("top", [])
        if top:
            test(
                "low-sum weights: max score <= weight sum (0.15)",
                top[0]["score"] <= 0.15 + 1e-9,
                f"score={top[0]['score']}",
            )
    except json.JSONDecodeError as exc:
        test("low-sum weights: JSON parseable", False, str(exc))


_test_low_sum_weights_warning()


# ─── 13. Unmigrated DB — missing required tables ────────────────────────────

print("\n🚫 Unmigrated DB — missing required tables")


def _test_unmigrated_db_bare_exits_cleanly():
    """A completely bare DB (no tables) must yield a clean nonzero exit, not a traceback."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="dream_test_bare_")
    os.close(fd)

    import io

    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    exited_code = None
    main_rc = None
    try:
        try:
            main_rc = dream.main(["--db", db_path, "--dry-run", "--json"])
        except SystemExit as exc:
            exited_code = exc.code
        stderr_out = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr
    try:
        os.unlink(db_path)
    except OSError:
        pass

    if exited_code is not None:
        test("bare DB: exits with code 1", exited_code == 1, f"code={exited_code}")
    else:
        test("bare DB: returns nonzero", main_rc not in (None, 0), f"rc={main_rc}")

    test(
        "bare DB: stderr mentions missing table(s)",
        "missing" in stderr_out and ("knowledge_entries" in stderr_out or "required" in stderr_out),
        f"stderr={stderr_out!r}",
    )
    test(
        "bare DB: stderr hints to run migrate",
        "migrate" in stderr_out,
        f"stderr={stderr_out!r}",
    )


_test_unmigrated_db_bare_exits_cleanly()


def _test_partial_schema_db_exits_cleanly():
    """A DB with only knowledge_entries (no recall/tags tables) must exit cleanly."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="dream_test_partial_")
    os.close(fd)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE knowledge_entries (id INTEGER PRIMARY KEY, title TEXT, category TEXT, confidence REAL, occurrence_count INTEGER)"
    )
    conn.commit()
    conn.close()

    import io

    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    exited_code = None
    main_rc = None
    try:
        try:
            main_rc = dream.main(["--db", db_path, "--dry-run", "--json"])
        except SystemExit as exc:
            exited_code = exc.code
        stderr_out = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr
    try:
        os.unlink(db_path)
    except OSError:
        pass

    if exited_code is not None:
        test("partial schema DB: exits with code 1", exited_code == 1, f"code={exited_code}")
    else:
        test("partial schema DB: returns nonzero", main_rc not in (None, 0), f"rc={main_rc}")

    test(
        "partial schema: stderr names a missing table",
        "entry_recall_stats" in stderr_out or "entry_concept_tags" in stderr_out,
        f"stderr={stderr_out!r}",
    )
    test(
        "partial schema: stderr hints to run migrate",
        "migrate" in stderr_out,
        f"stderr={stderr_out!r}",
    )


_test_partial_schema_db_exits_cleanly()


# ─── 14. MEMORY.md — basic generation (non-dry-run) ─────────────────────────

print("\n📄 MEMORY.md — basic generation")


def _test_memory_md_generated():
    """Non-dry-run run must write MEMORY.md to the specified path."""
    import tempfile

    db_path, conn = _make_db(
        entries=[
            {"title": "Alpha entry", "category": "pattern", "confidence": 1.0, "occurrence_count": 5},
            {"title": "Beta entry", "category": "mistake", "confidence": 0.9, "occurrence_count": 3},
        ],
        recall_stats=[
            {"entry_id": 1, "recall_count": 10, "unique_queries": 5, "last_recalled_at": "2025-01-01T00:00:00Z"},
            {"entry_id": 2, "recall_count": 8, "unique_queries": 4, "last_recalled_at": "2025-01-01T00:00:00Z"},
        ],
        tags=[
            {"entry_id": 1, "tag": "python"},
            {"entry_id": 1, "tag": "testing"},
            {"entry_id": 2, "tag": "auth"},
        ],
    )

    fd, mem_path = tempfile.mkstemp(suffix=".md", prefix="memory_test_")
    os.close(fd)
    os.unlink(mem_path)  # Let render_memory_md create it

    import io
    from pathlib import Path

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = dream.run_scoring(
            conn=conn,
            weights=W,
            min_score=0.0,
            min_recall=0,
            min_queries=0,
            dry_run=False,
            top_n=20,
            as_json=False,
            memory_output=Path(mem_path),
        )
    finally:
        sys.stdout = old_stdout
    _cleanup(db_path, conn)

    test("run_scoring with memory_output returns 0", rc == 0, f"rc={rc}")
    test("MEMORY.md file created", os.path.isfile(mem_path), f"path={mem_path}")

    if os.path.isfile(mem_path):
        with open(mem_path, encoding="utf-8") as f:
            mem_content = f.read()
        test("MEMORY.md starts with '# Promoted Memory'", mem_content.startswith("# Promoted Memory"), mem_content[:80])
        test("MEMORY.md contains 'Generated by'", "Generated by" in mem_content, mem_content[:200])
        test("MEMORY.md contains 'Promoted entries'", "Promoted entries" in mem_content, mem_content[:200])
        try:
            os.unlink(mem_path)
        except OSError:
            pass


_test_memory_md_generated()


# ─── 15. MEMORY.md — grouped by category, sorted by score desc ───────────────

print("\n📊 MEMORY.md — grouping and ordering")


def _test_memory_md_grouped_and_sorted():
    """Gate-passing entries must be grouped by category and sorted by score desc."""
    import tempfile
    from pathlib import Path

    # Create entries across two categories; control scores via recall / tag counts
    # We use min_score=0.0 and min_recall=0 so all entries pass gate
    db_path, conn = _make_db(
        entries=[
            {"title": "Pattern High", "category": "pattern", "confidence": 1.0, "occurrence_count": 20},
            {"title": "Pattern Low", "category": "pattern", "confidence": 0.1, "occurrence_count": 1},
            {"title": "Mistake Only", "category": "mistake", "confidence": 1.0, "occurrence_count": 5},
        ],
        recall_stats=[
            {"entry_id": 1, "recall_count": 100, "unique_queries": 50, "last_recalled_at": "2025-01-01T00:00:00Z"},
            {"entry_id": 2, "recall_count": 1, "unique_queries": 1, "last_recalled_at": None},
            {"entry_id": 3, "recall_count": 50, "unique_queries": 20, "last_recalled_at": "2025-03-01T00:00:00Z"},
        ],
        tags=[
            {"entry_id": 1, "tag": "python"},
            {"entry_id": 3, "tag": "auth"},
        ],
    )

    fd, mem_path = tempfile.mkstemp(suffix=".md", prefix="memory_test_")
    os.close(fd)
    os.unlink(mem_path)

    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        dream.run_scoring(
            conn=conn,
            weights=W,
            min_score=0.0,
            min_recall=0,
            min_queries=0,
            dry_run=False,
            top_n=20,
            as_json=False,
            memory_output=Path(mem_path),
        )
    finally:
        sys.stdout = old_stdout
    _cleanup(db_path, conn)

    test("MEMORY.md created for grouping test", os.path.isfile(mem_path), f"path={mem_path}")
    if not os.path.isfile(mem_path):
        return

    with open(mem_path, encoding="utf-8") as f:
        mem_content = f.read()

    # Both categories must be present as headings
    test("MEMORY.md has ## Mistake section", "## Mistake" in mem_content, mem_content)
    test("MEMORY.md has ## Pattern section", "## Pattern" in mem_content, mem_content)

    # Within pattern section, "Pattern High" must appear before "Pattern Low" (higher score)
    p_high_pos = mem_content.find("Pattern High")
    p_low_pos = mem_content.find("Pattern Low")
    test(
        "Pattern High appears before Pattern Low (score desc)",
        p_high_pos != -1 and p_low_pos != -1 and p_high_pos < p_low_pos,
        f"pos high={p_high_pos}, low={p_low_pos}",
    )

    try:
        os.unlink(mem_path)
    except OSError:
        pass


_test_memory_md_grouped_and_sorted()


# ─── 16. MEMORY.md — configurable output path ────────────────────────────────

print("\n📂 MEMORY.md — configurable output path")


def _test_memory_md_custom_path():
    """The memory_output path must be respected."""
    import tempfile
    from pathlib import Path

    db_path, conn = _make_db(
        entries=[{"title": "Custom path entry", "category": "decision", "confidence": 1.0, "occurrence_count": 2}],
        recall_stats=[
            {"entry_id": 1, "recall_count": 5, "unique_queries": 3, "last_recalled_at": "2025-01-01T00:00:00Z"}
        ],
        tags=[{"entry_id": 1, "tag": "config"}],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        custom_path = Path(tmpdir) / "subdir" / "custom_memory.md"
        # subdir does not exist; render_memory_md must create it

        import io

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = dream.run_scoring(
                conn=conn,
                weights=W,
                min_score=0.0,
                min_recall=0,
                min_queries=0,
                dry_run=False,
                top_n=20,
                as_json=False,
                memory_output=custom_path,
            )
        finally:
            sys.stdout = old_stdout
        _cleanup(db_path, conn)

        test("custom path run returns 0", rc == 0, f"rc={rc}")
        test("custom path file created", custom_path.is_file(), f"path={custom_path}")
        if custom_path.is_file():
            text = custom_path.read_text(encoding="utf-8")
            test("custom path file has content", "# Promoted Memory" in text, text[:80])


_test_memory_md_custom_path()


# ─── 17. MEMORY.md — idempotent overwrite ────────────────────────────────────

print("\n🔄 MEMORY.md — idempotent overwrite")


def _test_memory_md_overwrite():
    """Running twice must overwrite the file, not append."""
    import tempfile
    from pathlib import Path

    db_path, conn = _make_db(
        entries=[{"title": "Overwrite entry", "category": "pattern", "confidence": 1.0, "occurrence_count": 3}],
        recall_stats=[
            {"entry_id": 1, "recall_count": 5, "unique_queries": 3, "last_recalled_at": "2025-01-01T00:00:00Z"}
        ],
        tags=[{"entry_id": 1, "tag": "test"}],
    )

    fd, mem_path = tempfile.mkstemp(suffix=".md", prefix="memory_test_")
    os.close(fd)
    os.unlink(mem_path)

    import io
    from pathlib import Path as P

    mp = P(mem_path)

    def _run():
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            dream.run_scoring(
                conn=conn,
                weights=W,
                min_score=0.0,
                min_recall=0,
                min_queries=0,
                dry_run=False,
                top_n=20,
                as_json=False,
                memory_output=mp,
            )
        finally:
            sys.stdout = old_stdout

    _run()
    first_content = mp.read_text(encoding="utf-8") if mp.is_file() else ""

    _run()
    second_content = mp.read_text(encoding="utf-8") if mp.is_file() else ""

    _cleanup(db_path, conn)
    try:
        mp.unlink()
    except OSError:
        pass

    # The header embeds a live timestamp (ISO-8601, second precision), so two
    # consecutive runs may differ by one second.  Strip that line before comparing
    # to make the overwrite/no-append assertion deterministic.
    def _normalize(text: str) -> str:
        return "\n".join(ln for ln in text.splitlines() if not ln.startswith("> Generated by"))

    first_norm = _normalize(first_content)
    second_norm = _normalize(second_content)

    # Full overwrite: non-timestamp content must be identical (no duplication /
    # appending of sections, scores, or entry rows).
    test(
        "MEMORY.md content same on second run (no append, timestamp excluded)",
        first_norm == second_norm,
        f"first:\n{first_norm}\n\nsecond:\n{second_norm}",
    )
    # Paranoia: file must not grow — size on the two runs must differ by at most
    # the length of one timestamp string (20 chars) to catch any actual append.
    first_size = len(first_content)
    second_size = len(second_content)
    test(
        "MEMORY.md size unchanged on second run (within timestamp variance)",
        abs(first_size - second_size) <= 20,
        f"sizes: {first_size} vs {second_size}",
    )


_test_memory_md_overwrite()


# ─── 18. MEMORY.md — dry-run must NOT write ──────────────────────────────────

print("\n🚫 MEMORY.md — dry-run suppresses write")


def _test_memory_md_dry_run_no_write():
    """--dry-run must not create or modify MEMORY.md."""
    import tempfile
    from pathlib import Path

    db_path, conn = _make_db(
        entries=[{"title": "Dry entry", "category": "pattern", "confidence": 1.0, "occurrence_count": 2}],
    )

    fd, mem_path = tempfile.mkstemp(suffix=".md", prefix="memory_test_")
    os.close(fd)
    os.unlink(mem_path)

    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        dream.run_scoring(
            conn=conn,
            weights=W,
            min_score=0.0,
            min_recall=0,
            min_queries=0,
            dry_run=True,
            top_n=20,
            as_json=True,
            memory_output=Path(mem_path),
        )
    finally:
        sys.stdout = old_stdout
    _cleanup(db_path, conn)

    test("dry-run: MEMORY.md NOT created", not os.path.isfile(mem_path), f"file exists at {mem_path}")


_test_memory_md_dry_run_no_write()


# ─── 19. MEMORY.md — empty gate set produces placeholder ─────────────────────

print("\n📭 MEMORY.md — no entries pass gate")


def _test_memory_md_no_candidates():
    """When no entries pass the gate, MEMORY.md still gets a placeholder message."""
    import tempfile
    from pathlib import Path

    db_path, conn = _make_db(
        entries=[{"title": "Low entry", "category": "pattern", "confidence": 0.1, "occurrence_count": 1}],
    )

    fd, mem_path = tempfile.mkstemp(suffix=".md", prefix="memory_test_")
    os.close(fd)
    os.unlink(mem_path)

    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        dream.run_scoring(
            conn=conn,
            weights=W,
            min_score=0.99,  # very high gate — nothing passes
            min_recall=1000,
            min_queries=1000,
            dry_run=False,
            top_n=20,
            as_json=False,
            memory_output=Path(mem_path),
        )
    finally:
        sys.stdout = old_stdout
    _cleanup(db_path, conn)

    test("MEMORY.md created even with 0 candidates", os.path.isfile(mem_path), f"path={mem_path}")
    if os.path.isfile(mem_path):
        content = open(mem_path, encoding="utf-8").read()
        test("MEMORY.md has placeholder when empty", "No entries passed" in content, content[:200])
        try:
            os.unlink(mem_path)
        except OSError:
            pass


_test_memory_md_no_candidates()


# ─── 20. MEMORY.md via CLI --memory-output ───────────────────────────────────

print("\n🖥️  MEMORY.md — CLI --memory-output flag")


def _test_cli_memory_output():
    """CLI --memory-output must write MEMORY.md to the specified path."""
    import tempfile
    from pathlib import Path

    db_path, conn = _make_db(
        entries=[{"title": "CLI mem entry", "category": "pattern", "confidence": 1.0, "occurrence_count": 5}],
        recall_stats=[
            {"entry_id": 1, "recall_count": 5, "unique_queries": 3, "last_recalled_at": "2025-01-01T00:00:00Z"}
        ],
        tags=[{"entry_id": 1, "tag": "cli"}],
    )
    conn.close()

    fd, mem_path = tempfile.mkstemp(suffix=".md", prefix="memory_test_")
    os.close(fd)
    os.unlink(mem_path)

    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = dream.main(
            [
                "--db",
                db_path,
                "--json",
                "--min-score",
                "0.0",
                "--min-recall",
                "0",
                "--min-queries",
                "0",
                "--memory-output",
                mem_path,
            ]
        )
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    try:
        os.unlink(db_path)
    except OSError:
        pass

    test("CLI --memory-output returns 0", rc == 0, f"rc={rc}")
    test("CLI creates MEMORY.md", os.path.isfile(mem_path), f"path={mem_path}")

    # JSON output should include memory_output key
    try:
        data = json.loads(output)
        test("JSON includes 'memory_output' key", "memory_output" in data, f"keys={list(data)}")
        test(
            "JSON memory_output matches path", data.get("memory_output") == mem_path, f"got {data.get('memory_output')}"
        )
    except json.JSONDecodeError as e:
        test("CLI JSON parseable", False, str(e))

    try:
        os.unlink(mem_path)
    except OSError:
        pass


_test_cli_memory_output()


# ─── 21. MEMORY.md via CLI --dry-run never writes ────────────────────────────

print("\n🚫 MEMORY.md — CLI --dry-run no write")


def _test_cli_dry_run_no_memory():
    """CLI --dry-run must NOT write MEMORY.md even when --memory-output is given."""
    import tempfile
    from pathlib import Path

    db_path, conn = _make_db(
        entries=[{"title": "DryRun mem", "category": "pattern", "confidence": 1.0}],
    )
    conn.close()

    fd, mem_path = tempfile.mkstemp(suffix=".md", prefix="memory_test_")
    os.close(fd)
    os.unlink(mem_path)

    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = dream.main(
            [
                "--db",
                db_path,
                "--dry-run",
                "--json",
                "--min-score",
                "0.0",
                "--min-recall",
                "0",
                "--min-queries",
                "0",
                "--memory-output",
                mem_path,
            ]
        )
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    try:
        os.unlink(db_path)
    except OSError:
        pass

    test("CLI --dry-run returns 0", rc == 0, f"rc={rc}")
    test("CLI --dry-run: MEMORY.md NOT created", not os.path.isfile(mem_path), f"file exists at {mem_path}")

    # JSON memory_output should be null for dry-run
    try:
        data = json.loads(output)
        test(
            "dry-run JSON memory_output is null", data.get("memory_output") is None, f"got {data.get('memory_output')}"
        )
    except json.JSONDecodeError as e:
        test("CLI dry-run JSON parseable", False, str(e))


_test_cli_dry_run_no_memory()


# ─── 22. MEMORY.md — rendered table includes recall_count ────────────────────

print("\n🔢 MEMORY.md — recall_count column in rendered table")


def _test_memory_md_recall_count_rendered():
    """Each promoted entry must include recall_count in the rendered MEMORY.md table."""
    import tempfile
    from pathlib import Path

    db_path, conn = _make_db(
        entries=[
            {"title": "High recall entry", "category": "pattern", "confidence": 1.0, "occurrence_count": 10},
            {"title": "Low recall entry", "category": "pattern", "confidence": 0.8, "occurrence_count": 5},
        ],
        recall_stats=[
            {"entry_id": 1, "recall_count": 42, "unique_queries": 10, "last_recalled_at": "2025-01-01T00:00:00Z"},
            {"entry_id": 2, "recall_count": 7, "unique_queries": 3, "last_recalled_at": "2025-02-01T00:00:00Z"},
        ],
        tags=[
            {"entry_id": 1, "tag": "python"},
        ],
    )

    fd, mem_path = tempfile.mkstemp(suffix=".md", prefix="memory_recall_test_")
    os.close(fd)
    os.unlink(mem_path)

    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        dream.run_scoring(
            conn=conn,
            weights=W,
            min_score=0.0,
            min_recall=0,
            min_queries=0,
            dry_run=False,
            top_n=20,
            as_json=False,
            memory_output=Path(mem_path),
        )
    finally:
        sys.stdout = old_stdout
    _cleanup(db_path, conn)

    test("MEMORY.md created for recall_count test", os.path.isfile(mem_path), f"path={mem_path}")
    if not os.path.isfile(mem_path):
        return

    with open(mem_path, encoding="utf-8") as f:
        mem_content = f.read()

    # Table header must have a Recalls column
    test(
        "MEMORY.md table header contains 'Recalls'",
        "Recalls" in mem_content,
        mem_content[:500],
    )
    # recall_count values must appear in rendered output
    test(
        "MEMORY.md contains recall_count value 42",
        "| 42 |" in mem_content or "42 |" in mem_content,
        mem_content,
    )
    test(
        "MEMORY.md contains recall_count value 7",
        "| 7 |" in mem_content or "7 |" in mem_content,
        mem_content,
    )

    try:
        os.unlink(mem_path)
    except OSError:
        pass


_test_memory_md_recall_count_rendered()


# ─── 23. MEMORY.md — write failure reported cleanly (no traceback) ───────────

print("\n💥 MEMORY.md — write failure path")


def _test_memory_md_write_failure_clean():
    """When render_memory_md raises OSError, run_scoring must return nonzero and
    print a message to stderr instead of propagating a raw traceback."""
    import io
    import unittest.mock
    from pathlib import Path

    db_path, conn = _make_db(
        entries=[
            {"title": "Stable entry", "category": "pattern", "confidence": 1.0, "occurrence_count": 5},
        ],
        recall_stats=[
            {"entry_id": 1, "recall_count": 10, "unique_queries": 5, "last_recalled_at": "2025-01-01T00:00:00Z"},
        ],
    )

    fake_mem_path = Path("/nonexistent_dir_that_will_fail/MEMORY.md")

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    captured_stderr = io.StringIO()
    sys.stdout = io.StringIO()
    sys.stderr = captured_stderr

    try:
        with unittest.mock.patch.object(
            Path,
            "mkdir",
            side_effect=OSError("permission denied"),
        ):
            rc = dream.run_scoring(
                conn=conn,
                weights=W,
                min_score=0.0,
                min_recall=0,
                min_queries=0,
                dry_run=False,
                top_n=20,
                as_json=False,
                memory_output=fake_mem_path,
            )
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    _cleanup(db_path, conn)

    stderr_out = captured_stderr.getvalue()

    test("write failure: run_scoring returns nonzero", rc != 0, f"rc={rc}")
    test(
        "write failure: stderr contains 'error'",
        "error" in stderr_out.lower(),
        f"stderr={stderr_out!r}",
    )
    test(
        "write failure: stderr contains 'MEMORY.md' or path fragment",
        "MEMORY.md" in stderr_out or "nonexistent_dir" in stderr_out or "permission" in stderr_out,
        f"stderr={stderr_out!r}",
    )


_test_memory_md_write_failure_clean()


# ─── 24. MEMORY.md — DB committed before write failure, scoring preserved ────

print("\n💾 MEMORY.md — DB persisted even when MEMORY.md write fails")


def _test_memory_md_db_committed_before_write_failure():
    """Scores must be committed to the DB even when the subsequent MEMORY.md write fails."""
    import io
    import unittest.mock
    from pathlib import Path

    db_path, conn = _make_db(
        entries=[
            {"title": "Persist me", "category": "pattern", "confidence": 1.0, "occurrence_count": 3},
        ],
        recall_stats=[
            {"entry_id": 1, "recall_count": 5, "unique_queries": 2, "last_recalled_at": "2025-01-01T00:00:00Z"},
        ],
    )

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        with unittest.mock.patch.object(
            Path,
            "mkdir",
            side_effect=OSError("disk full"),
        ):
            dream.run_scoring(
                conn=conn,
                weights=W,
                min_score=0.0,
                min_recall=0,
                min_queries=0,
                dry_run=False,
                top_n=20,
                as_json=False,
                memory_output=Path("/bad/path/MEMORY.md"),
            )
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # DB row must have been committed despite the MEMORY.md failure
    row = conn.execute("SELECT COUNT(*) FROM entry_dream_scores").fetchone()
    _cleanup(db_path, conn)

    test(
        "DB scores committed despite MEMORY.md write failure",
        row[0] >= 1,
        f"entry_dream_scores row count={row[0]}",
    )


_test_memory_md_db_committed_before_write_failure()


print(f"\n{'=' * 50}")
print(f"  PASS: {PASS}  FAIL: {FAIL}")
print(f"{'=' * 50}")
if FAIL:
    sys.exit(1)
