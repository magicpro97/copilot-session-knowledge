#!/usr/bin/env python3
"""test_browse_session_detail.py — Unit tests for browse/api/session_detail.py.

Focus: root-level `has_operator_runs` contract added by issue #518. A separate
file is justified because existing `tests/test_browse_api_helpers.py` only
covers helpers in `browse/api/_common.py`, and `tests/test_browse_api_contract.py`
is a live integration runner against a real server, not unit-level coverage of
the session-detail route.
"""

import json
import os
import sys
import sqlite3
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.api import session_detail  # noqa: E402
from browse.api._common import normalize_session_meta  # noqa: E402

_PASS = 0
_FAIL = 0


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


# ── Fake DB helpers ───────────────────────────────────────────────────────────

def _make_db_with_session(session_id: str = "33169957-0dc1-4998-86c0-d2beba02e8b4") -> sqlite3.Connection:
    """Build an in-memory SQLite with the minimal schema session_detail.py needs."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            path TEXT, summary TEXT, source TEXT,
            event_count_estimate INTEGER,
            fts_indexed_at TEXT, file_mtime REAL,
            indexed_at_r TEXT, indexed_at TEXT,
            total_checkpoints INTEGER, total_research INTEGER,
            total_files INTEGER, has_plan INTEGER
        );
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            session_id TEXT, seq INTEGER,
            title TEXT, doc_type TEXT
        );
        CREATE TABLE sections (
            id INTEGER PRIMARY KEY,
            document_id INTEGER,
            section_name TEXT, content TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO sessions (id, source, total_checkpoints, total_research, total_files, has_plan) "
        "VALUES (?, 'copilot', 1, 0, 0, 0)",
        (session_id,),
    )
    db.commit()
    return db


def _call(db, session_id: str):
    body, ct, status = session_detail.handle_api_session_detail(
        db, params={}, token=None, nonce=None, session_id=session_id
    )
    payload = json.loads(body) if status == 200 else None
    return payload, ct, status


# ── Contract: has_operator_runs root key ──────────────────────────────────────

VALID_ID = "33169957-0dc1-4998-86c0-d2beba02e8b4"


def test_knowledge_only_session_has_operator_runs_false(monkeypatch):
    db = _make_db_with_session(VALID_ID)
    # Operator store has no record for this id → False.
    monkeypatch.setattr(session_detail.operator_console, "get_session", lambda _id: None)
    payload, ct, status = _call(db, VALID_ID)
    test("knowledge-only: status 200", status == 200)
    test("knowledge-only: content type", ct == "application/json")
    test("knowledge-only: root has_operator_runs present", "has_operator_runs" in (payload or {}))
    test("knowledge-only: has_operator_runs is False", payload.get("has_operator_runs") is False)


def test_operator_backed_session_has_operator_runs_true(monkeypatch):
    db = _make_db_with_session(VALID_ID)
    monkeypatch.setattr(
        session_detail.operator_console, "get_session", lambda _id: {"id": _id, "status": "idle"}
    )
    payload, _, status = _call(db, VALID_ID)
    test("operator-backed: status 200", status == 200)
    test("operator-backed: has_operator_runs True", payload.get("has_operator_runs") is True)


def test_operator_lookup_failure_defaults_false(monkeypatch):
    db = _make_db_with_session(VALID_ID)

    def _boom(_id):
        raise OSError("operator store unavailable")

    monkeypatch.setattr(session_detail.operator_console, "get_session", _boom)
    payload, _, status = _call(db, VALID_ID)
    test("lookup failure: status 200", status == 200)
    test("lookup failure: has_operator_runs False", payload.get("has_operator_runs") is False)


def test_meta_and_timeline_unchanged():
    db = _make_db_with_session(VALID_ID)
    payload, _, status = _call(db, VALID_ID)
    test("shape: status 200", status == 200)
    test("shape: meta present", isinstance(payload.get("meta"), dict))
    test("shape: timeline present", isinstance(payload.get("timeline"), list))
    test("shape: meta has no has_operator_runs", "has_operator_runs" not in payload["meta"])


def test_invalid_session_id_unchanged():
    db = _make_db_with_session(VALID_ID)
    body, _, status = session_detail.handle_api_session_detail(
        db, params={}, token=None, nonce=None, session_id="not!a!valid!id"
    )
    data = json.loads(body)
    test("invalid id: 400", status == 400)
    test("invalid id: code BAD_SESSION_ID", data.get("code") == "BAD_SESSION_ID")
    test("invalid id: no has_operator_runs leakage", "has_operator_runs" not in data)


def test_missing_session_unchanged():
    db = _make_db_with_session(VALID_ID)
    body, _, status = session_detail.handle_api_session_detail(
        db, params={}, token=None, nonce=None,
        session_id="11111111-1111-1111-1111-111111111111",
    )
    data = json.loads(body)
    test("missing: 404", status == 404)
    test("missing: code SESSION_NOT_FOUND", data.get("code") == "SESSION_NOT_FOUND")
    test("missing: no has_operator_runs leakage", "has_operator_runs" not in data)


# Guard: normalize_session_meta must NOT carry has_operator_runs.
def test_normalize_session_meta_does_not_introduce_has_operator_runs():
    result = normalize_session_meta({"event_count_estimate": 1})
    test("guard: no has_operator_runs in meta", "has_operator_runs" not in (result or {}))


# ── Minimal monkeypatch shim (no pytest dependency) ──────────────────────────

class _MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value):
        original = getattr(target, name)
        self._undo.append((target, name, original))
        setattr(target, name, value)

    def undo(self):
        for target, name, original in reversed(self._undo):
            setattr(target, name, original)
        self._undo.clear()


if __name__ == "__main__":
    for fn in (
        test_knowledge_only_session_has_operator_runs_false,
        test_operator_backed_session_has_operator_runs_true,
        test_operator_lookup_failure_defaults_false,
    ):
        mp = _MonkeyPatch()
        try:
            fn(mp)
        finally:
            mp.undo()

    test_meta_and_timeline_unchanged()
    test_invalid_session_id_unchanged()
    test_missing_session_unchanged()
    test_normalize_session_meta_does_not_introduce_has_operator_runs()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
