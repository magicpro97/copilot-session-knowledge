#!/usr/bin/env python3
"""tests/test_checkpoints_route.py — Flight Recorder v3 checkpoint summary route.

GET /api/session/{id}/checkpoints   (synthesis §4a, §6a)
GET /api/sessions/{id}/checkpoints  (plural alias)

Tests:
  CK1  Valid session with index.md + checkpoint files → 200, correct envelope
  CK2  schema_version == "1"
  CK3  session_id echoed
  CK4  total reflects emitted summaries
  CK5  Each summary has exactly the contracted keys (no body text leak)
  CK6  section flags reflect tag-header presence (overview only / overview+history / all six)
  CK7  byte_size matches the underlying file size
  CK8  Invalid (non-UUID4) session_id → 404, no value leak
  CK9  Unknown UUID4 session_id (no dir) → 404
  CK10 Missing checkpoints/index.md → 404
  CK11 Symlinked index.md → 404
  CK12 Symlinked checkpoint file is silently dropped (not in response)
  CK13 Oversize index.md (>1 MB) → 413 with empty body
  CK14 Index entry referencing a non-safe basename (slashes / traversal) is dropped
  CK15 Plural alias /api/sessions/{id}/checkpoints returns identical shape
  CK16 ≤200 entries enforced (cap)
  CK17 Title is scrubbed (bearer-token in title is redacted)
  CK18 No auth → 401 (debug route auth gate)
  CK19 ?token= query-string rejected for debug route
  CK20 Cookie auth → 200

Run: python3 tests/test_checkpoints_route.py
"""

import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import uuid
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

_CLI_STATE_DIR_HANDLE = tempfile.TemporaryDirectory()
_CLI_STATE_DIR = Path(_CLI_STATE_DIR_HANDLE.name)
os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)

_OP_STATE_DIR_HANDLE = tempfile.TemporaryDirectory()
_OP_STATE_DIR = Path(_OP_STATE_DIR_HANDLE.name)
os.environ["COPILOT_OPERATOR_STATE"] = str(_OP_STATE_DIR)

import browse.routes.checkpoints  # noqa: E402,F401 — registers routes
import browse.routes.health  # noqa: E402,F401
from browse.core.server import _make_handler_class  # noqa: E402

_PASS = 0
_FAIL = 0
_TOKEN = "test-cp-token-frv3"


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


def _make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def _make_server(token: str = _TOKEN) -> tuple:
    HandlerClass = _make_handler_class(_make_db(), token)
    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _bearer(port: int, path: str, token: str = _TOKEN) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    conn.request("GET", path, headers={"Authorization": f"Bearer {token}"})
    return conn.getresponse()


def _cookie(port: int, path: str, token: str = _TOKEN) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    conn.request("GET", path, headers={"Cookie": f"browse_token={token}"})
    return conn.getresponse()


def _no_auth(port: int, path: str) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    conn.request("GET", path)
    return conn.getresponse()


def _token_qs(port: int, path: str) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    sep = "&" if "?" in path else "?"
    conn.request("GET", f"{path}{sep}token={_TOKEN}")
    return conn.getresponse()


def _make_session_dir(session_uuid: str | None = None) -> tuple[str, Path]:
    sid = session_uuid or str(uuid.uuid4())
    d = _CLI_STATE_DIR / sid
    (d / "checkpoints").mkdir(parents=True, exist_ok=True)
    return sid, d


def _write_index_md(session_dir: Path, rows: list[tuple[int, str, str]]) -> None:
    """rows: list of (seq, title, file_basename)."""
    lines = [
        "# Checkpoint History",
        "",
        "| # | Title | File |",
        "|---|-------|------|",
    ]
    for seq, title, fname in rows:
        lines.append(f"| {seq} | {title} | {fname} |")
    (session_dir / "checkpoints" / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_checkpoint(session_dir: Path, basename: str, sections: list[str], extra: str = "") -> None:
    """Write a checkpoint file containing the listed `<section>` headers."""
    blocks = []
    for s in sections:
        blocks.append(f"<{s}>\nbody text for {s}\n</{s}>\n")
    text = "\n".join(blocks) + extra
    (session_dir / "checkpoints" / basename).write_text(text, encoding="utf-8")


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_happy_path():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        _write_index_md(
            d,
            [
                (1, "Infrastructure fix", "001-infra.md"),
                (2, "Wave 1 shipped", "002-wave1.md"),
            ],
        )
        _write_checkpoint(d, "001-infra.md", ["overview", "history"])
        _write_checkpoint(d, "002-wave1.md", list(
            ("overview", "history", "work_done", "technical_details", "important_files", "next_steps")
        ))

        resp = _bearer(port, f"/api/session/{sid}/checkpoints")
        body = resp.read()
        test("CK1 status=200", resp.status == 200)
        test("CK1 content-type json", "application/json" in resp.getheader("content-type", ""))
        data = json.loads(body)
        test("CK2 schema_version=1", data.get("schema_version") == "1")
        test("CK3 session_id echoed", data.get("session_id") == sid)
        test("CK4 total=2", data.get("total") == 2)
        items = data.get("checkpoints") or []
        test("CK1 checkpoints list", isinstance(items, list) and len(items) == 2)

        first = items[0]
        contracted = {"seq", "title", "file_basename", "byte_size", "mtime_iso", "sections"}
        test("CK5 only contracted keys", set(first.keys()) == contracted)
        test("CK5 sections keys exact", set(first["sections"].keys()) == {
            "overview", "history", "work_done", "technical_details", "important_files", "next_steps",
        })
        test("CK5 no body text leaking", "body text" not in json.dumps(data))
        test("CK6 section flags: overview true", first["sections"]["overview"] is True)
        test("CK6 section flags: history true", first["sections"]["history"] is True)
        test("CK6 section flags: work_done false", first["sections"]["work_done"] is False)
        second = items[1]
        test("CK6 all six sections true", all(second["sections"].values()))

        # byte_size matches the real file size on disk.
        real = (d / "checkpoints" / "001-infra.md").stat().st_size
        test("CK7 byte_size matches", first["byte_size"] == real)

        # CK21 mtime_iso: present, strict UTC-Z ISO string, monotonically
        # close to the file's real lstat().st_mtime (within one second).
        import re as _re
        import datetime as _dt
        m = first["mtime_iso"]
        test("CK21 mtime_iso is string", isinstance(m, str))
        test(
            "CK21 mtime_iso strict ISO-UTC-Z",
            isinstance(m, str)
            and bool(_re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$", m)),
        )
        real_mt = (d / "checkpoints" / "001-infra.md").stat().st_mtime
        parsed_mt = _dt.datetime.fromisoformat(m.replace("Z", "+00:00")).timestamp()
        test("CK21 mtime_iso matches file mtime", abs(parsed_mt - real_mt) < 1.0)
    finally:
        srv.shutdown()


def test_invalid_uuid_404():
    srv, port = _make_server()
    try:
        resp = _bearer(port, "/api/session/not-a-uuid/checkpoints")
        body = resp.read()
        test("CK8 invalid uuid 404", resp.status == 404)
        test("CK8 no value leak", b"not-a-uuid" not in body)
    finally:
        srv.shutdown()


def test_unknown_uuid_404():
    srv, port = _make_server()
    try:
        unknown = str(uuid.uuid4())
        resp = _bearer(port, f"/api/session/{unknown}/checkpoints")
        test("CK9 unknown uuid 404", resp.status == 404)
    finally:
        srv.shutdown()


def test_missing_index_404():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        # no index.md written
        resp = _bearer(port, f"/api/session/{sid}/checkpoints")
        test("CK10 missing index 404", resp.status == 404)
    finally:
        srv.shutdown()


def test_symlink_index_404():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        # Write a real target file outside the session dir; symlink to it.
        target = d / "_real.md"
        target.write_text("| 1 | t | 001.md |\n", encoding="utf-8")
        link = d / "checkpoints" / "index.md"
        # POSIX-only: os.symlink may be unavailable / unprivileged on Windows.
        if os.name == "nt":
            print("  SKIP  CK11 os.symlink unsupported on Windows")
            return
        try:  # os.name guard above
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError):
            print("  SKIP  CK11 symlink unsupported")
            return
        resp = _bearer(port, f"/api/session/{sid}/checkpoints")
        test("CK11 symlink index 404", resp.status == 404)
    finally:
        srv.shutdown()


def test_symlinked_checkpoint_file_dropped():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        _write_index_md(d, [(1, "Real", "001.md"), (2, "Symlinked", "002.md")])
        _write_checkpoint(d, "001.md", ["overview"])
        # Make 002.md a symlink to a target outside checkpoints/
        target = d / "_evil.md"
        target.write_text("<overview>\n</overview>", encoding="utf-8")
        link = d / "checkpoints" / "002.md"
        # POSIX-only: os.symlink may be unavailable / unprivileged on Windows.
        if os.name == "nt":
            print("  SKIP  CK12 os.symlink unsupported on Windows")
            return
        try:  # os.name guard above
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError):
            print("  SKIP  CK12 symlink unsupported")
            return
        resp = _bearer(port, f"/api/session/{sid}/checkpoints")
        data = json.loads(resp.read())
        test("CK12 symlinked checkpoint dropped", data["total"] == 1)
        test("CK12 only 001.md in response", data["checkpoints"][0]["file_basename"] == "001.md")
    finally:
        srv.shutdown()


def test_oversize_index_413():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        # write a >1 MB index.md
        big = "| 1 | filler | 001.md |\n" * 60000  # easily >1 MB
        (d / "checkpoints" / "index.md").write_text(big, encoding="utf-8")
        resp = _bearer(port, f"/api/session/{sid}/checkpoints")
        body = resp.read()
        test("CK13 oversize 413", resp.status == 413)
        test("CK13 empty body", body == b"")
    finally:
        srv.shutdown()


def test_unsafe_basename_dropped():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        _write_index_md(
            d,
            [
                (1, "Real", "001.md"),
                (2, "Traversal", "../../etc/passwd"),
                (3, "Slash", "foo/bar.md"),
                (4, "NUL", "ok\x00.md"),
            ],
        )
        _write_checkpoint(d, "001.md", ["overview"])
        resp = _bearer(port, f"/api/session/{sid}/checkpoints")
        data = json.loads(resp.read())
        test("CK14 only safe basename kept", data["total"] == 1)
        test("CK14 no traversal in body", "passwd" not in json.dumps(data))
    finally:
        srv.shutdown()


def test_plural_alias():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        _write_index_md(d, [(1, "X", "001.md")])
        _write_checkpoint(d, "001.md", ["overview"])
        resp_a = _bearer(port, f"/api/session/{sid}/checkpoints")
        resp_b = _bearer(port, f"/api/sessions/{sid}/checkpoints")
        a = json.loads(resp_a.read())
        b = json.loads(resp_b.read())
        test("CK15 plural alias same shape", a == b)
    finally:
        srv.shutdown()


def test_max_entries_cap():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        rows = [(i, f"T{i}", f"{i:03d}.md") for i in range(1, 251)]  # 250 rows
        _write_index_md(d, rows)
        # Only write 230 checkpoint files (some entries will reference non-existent files).
        for i in range(1, 231):
            _write_checkpoint(d, f"{i:03d}.md", ["overview"])
        resp = _bearer(port, f"/api/session/{sid}/checkpoints")
        data = json.loads(resp.read())
        test("CK16 cap <=200", data["total"] <= 200)
    finally:
        srv.shutdown()


def test_title_scrubbed():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        # Bearer token embedded in title — must be redacted.
        _write_index_md(d, [(1, "leaky Bearer abcdefABCDEFabcdef123 token", "001.md")])
        _write_checkpoint(d, "001.md", ["overview"])
        resp = _bearer(port, f"/api/session/{sid}/checkpoints")
        data = json.loads(resp.read())
        title = data["checkpoints"][0]["title"]
        test("CK17 title not raw token", "abcdefABCDEFabcdef123" not in title)
        test("CK17 title contains redaction marker", "[REDACTED]" in title or "Bearer" not in title)
    finally:
        srv.shutdown()


def test_auth_gates():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        _write_index_md(d, [(1, "X", "001.md")])
        _write_checkpoint(d, "001.md", ["overview"])

        resp1 = _no_auth(port, f"/api/session/{sid}/checkpoints")
        resp1.read()
        test("CK18 no auth 401", resp1.status == 401)

        resp2 = _token_qs(port, f"/api/session/{sid}/checkpoints")
        resp2.read()
        test("CK19 ?token= rejected", resp2.status == 401)

        resp3 = _cookie(port, f"/api/session/{sid}/checkpoints")
        resp3.read()
        test("CK20 cookie 200", resp3.status == 200)
    finally:
        srv.shutdown()


def test_unit_helpers():
    """Direct unit calls on shared helpers."""
    from browse.routes._checkpoint_index import (  # noqa: PLC0415
        UUID4_RE,
        is_safe_file_basename,
        parse_checkpoint_index,
        resolve_safe_child,
    )

    test("U1 UUID4_RE accepts valid",
         bool(UUID4_RE.match("33169957-0dc1-4998-86c0-d2beba02e8b4")))
    test("U2 UUID4_RE rejects uppercase",
         not bool(UUID4_RE.match("33169957-0DC1-4998-86C0-D2BEBA02E8B4")))
    test("U3 is_safe_file_basename accepts 001.md", is_safe_file_basename("001-foo.md"))
    test("U4 is_safe_file_basename rejects slash", not is_safe_file_basename("a/b.md"))
    test("U5 is_safe_file_basename rejects traversal", not is_safe_file_basename(".."))
    test("U6 resolve_safe_child rejects bad uuid",
         resolve_safe_child("not-a-uuid", "checkpoints", "index.md") is None)
    test("U7 resolve_safe_child rejects slash-in-part",
         resolve_safe_child(str(uuid.uuid4()), "../etc", "passwd") is None)

    # parse_checkpoint_index: real file
    sid, d = _make_session_dir()
    _write_index_md(d, [(1, "T1", "001.md"), (2, "T2", "002.md")])
    parsed = parse_checkpoint_index(d / "checkpoints" / "index.md")
    test("U8 parser returns 2 rows", len(parsed) == 2)
    test("U9 parser shape correct",
         set(parsed[0].keys()) == {"seq", "title", "file_basename"})


def _run_all() -> None:
    test_unit_helpers()
    test_happy_path()
    test_invalid_uuid_404()
    test_unknown_uuid_404()
    test_missing_index_404()
    test_symlink_index_404()
    test_symlinked_checkpoint_file_dropped()
    test_oversize_index_413()
    test_unsafe_basename_dropped()
    test_plural_alias()
    test_max_entries_cap()
    test_title_scrubbed()
    test_auth_gates()

    total = _PASS + _FAIL
    print("=" * 60)
    print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
