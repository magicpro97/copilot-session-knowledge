#!/usr/bin/env python3
"""
test_code_search_benchmark.py — Benchmark harness for 4 code-search approaches.

Approaches:
  1. FTS5 trigram  — SQLite trigram tokenizer (in-process, no deps)
  2. BM25          — keyword BM25 scoring in pure Python
  3. sqlite-vec    — vector extension for semantic search (skipped if unavailable)
  4. ripgrep       — shell out to `rg` (skipped if not on PATH)

Usage:
    python3 tests/test_code_search_benchmark.py             # smoke test (<5 s)
    python3 tests/test_code_search_benchmark.py --profile   # full metrics + markdown table

Smoke test: verifies all available approaches complete without error.
--profile:  measures latency (ms), Precision@5, Recall@5, MRR; prints markdown table.
"""

import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Corpus: 50 synthetic code entries (function_name, docstring, file_path)
# ---------------------------------------------------------------------------

_CORPUS: list[dict] = [
    # --- auth / security (0-4) ---
    {
        "id": 0,
        "fn": "authenticate_user",
        "doc": "Verify user credentials against the database. Returns session token on success.",
        "path": "src/auth/login.py",
    },
    {
        "id": 1,
        "fn": "hash_password",
        "doc": "Hash a plaintext password using bcrypt. Salt is generated automatically.",
        "path": "src/auth/passwords.py",
    },
    {
        "id": 2,
        "fn": "verify_token",
        "doc": "Decode and validate a JWT authentication token. Raises InvalidToken if expired.",
        "path": "src/auth/tokens.py",
    },
    {
        "id": 3,
        "fn": "check_permissions",
        "doc": "Assert that the current user has the required role or permission level.",
        "path": "src/auth/rbac.py",
    },
    {
        "id": 4,
        "fn": "generate_api_key",
        "doc": "Generate a cryptographically secure random API key for user authentication.",
        "path": "src/auth/apikeys.py",
    },
    # --- database (5-9) ---
    {
        "id": 5,
        "fn": "connect_database",
        "doc": "Open a connection to the SQLite database at the given path. Returns connection object.",
        "path": "src/db/connection.py",
    },
    {
        "id": 6,
        "fn": "execute_query",
        "doc": "Execute a parameterized SQL query and return all result rows.",
        "path": "src/db/query.py",
    },
    {
        "id": 7,
        "fn": "insert_record",
        "doc": "Insert a new row into the specified database table. Returns last inserted row id.",
        "path": "src/db/crud.py",
    },
    {
        "id": 8,
        "fn": "fetch_rows",
        "doc": "Fetch multiple rows from the database using cursor iteration.",
        "path": "src/db/fetch.py",
    },
    {
        "id": 9,
        "fn": "close_connection",
        "doc": "Commit pending changes and close the database connection safely.",
        "path": "src/db/connection.py",
    },
    # --- file I/O (10-14) ---
    {
        "id": 10,
        "fn": "read_file",
        "doc": "Read the entire contents of a file and return as a string. Handles UTF-8 encoding.",
        "path": "src/io/files.py",
    },
    {
        "id": 11,
        "fn": "write_file",
        "doc": "Write text content to a file, creating it if it does not exist.",
        "path": "src/io/files.py",
    },
    {
        "id": 12,
        "fn": "list_directory",
        "doc": "List all files and subdirectories inside a given directory path.",
        "path": "src/io/filesystem.py",
    },
    {
        "id": 13,
        "fn": "delete_file",
        "doc": "Delete a file from the filesystem. Raises FileNotFoundError if missing.",
        "path": "src/io/files.py",
    },
    {
        "id": 14,
        "fn": "copy_file",
        "doc": "Copy a source file to a destination path, overwriting if necessary.",
        "path": "src/io/files.py",
    },
    # --- HTTP / network (15-19) ---
    {
        "id": 15,
        "fn": "send_request",
        "doc": "Send an HTTP request to the given URL using urllib. Returns response bytes.",
        "path": "src/http/client.py",
    },
    {
        "id": 16,
        "fn": "get_response",
        "doc": "Perform an HTTP GET request and parse the response body as text.",
        "path": "src/http/client.py",
    },
    {
        "id": 17,
        "fn": "post_json",
        "doc": "POST a JSON payload to a remote endpoint and return the parsed response.",
        "path": "src/http/client.py",
    },
    {
        "id": 18,
        "fn": "download_file",
        "doc": "Download a remote file over HTTP and save it to a local path.",
        "path": "src/http/transfer.py",
    },
    {
        "id": 19,
        "fn": "upload_file",
        "doc": "Upload a local file to a remote server via HTTP multipart POST.",
        "path": "src/http/transfer.py",
    },
    # --- JSON (20-24) ---
    {
        "id": 20,
        "fn": "parse_json",
        "doc": "Parse a JSON string and return the corresponding Python object.",
        "path": "src/utils/json_utils.py",
    },
    {
        "id": 21,
        "fn": "serialize_json",
        "doc": "Serialize a Python dict or list to a compact JSON string.",
        "path": "src/utils/json_utils.py",
    },
    {
        "id": 22,
        "fn": "validate_schema",
        "doc": "Validate a JSON document against a given JSON Schema definition.",
        "path": "src/utils/json_utils.py",
    },
    {
        "id": 23,
        "fn": "merge_json",
        "doc": "Deep-merge two JSON-compatible dicts, with right-hand side values winning.",
        "path": "src/utils/json_utils.py",
    },
    {
        "id": 24,
        "fn": "pretty_print_json",
        "doc": "Format a Python object as an indented, human-readable JSON string.",
        "path": "src/utils/json_utils.py",
    },
    # --- logging (25-29) ---
    {
        "id": 25,
        "fn": "log_error",
        "doc": "Emit an ERROR-level log message with optional exception traceback.",
        "path": "src/logging/logger.py",
    },
    {
        "id": 26,
        "fn": "log_info",
        "doc": "Emit an INFO-level log message to the configured log handler.",
        "path": "src/logging/logger.py",
    },
    {
        "id": 27,
        "fn": "log_debug",
        "doc": "Emit a DEBUG-level log entry. Suppressed unless debug mode is active.",
        "path": "src/logging/logger.py",
    },
    {
        "id": 28,
        "fn": "setup_logger",
        "doc": "Configure the root logger with file and console handlers.",
        "path": "src/logging/setup.py",
    },
    {
        "id": 29,
        "fn": "format_log_message",
        "doc": "Format a log message with timestamp, level, and caller context.",
        "path": "src/logging/formatter.py",
    },
    # --- async (30-34) ---
    {
        "id": 30,
        "fn": "async_fetch",
        "doc": "Async coroutine that fetches data from a URL using aiohttp.",
        "path": "src/async_ops/fetch.py",
    },
    {
        "id": 31,
        "fn": "await_task",
        "doc": "Await a single asyncio Task and return its result.",
        "path": "src/async_ops/tasks.py",
    },
    {
        "id": 32,
        "fn": "run_coroutine",
        "doc": "Run an async coroutine inside a new event loop with asyncio.run().",
        "path": "src/async_ops/runner.py",
    },
    {
        "id": 33,
        "fn": "gather_tasks",
        "doc": "Gather multiple async coroutines and await all results concurrently.",
        "path": "src/async_ops/tasks.py",
    },
    {
        "id": 34,
        "fn": "cancel_task",
        "doc": "Cancel a running asyncio Task and suppress the CancelledError.",
        "path": "src/async_ops/tasks.py",
    },
    # --- validation (35-39) ---
    {
        "id": 35,
        "fn": "validate_email",
        "doc": "Validate that a string is a well-formed email address using regex.",
        "path": "src/validation/email.py",
    },
    {
        "id": 36,
        "fn": "validate_url",
        "doc": "Check that a string is a valid HTTP or HTTPS URL.",
        "path": "src/validation/url.py",
    },
    {
        "id": 37,
        "fn": "validate_date",
        "doc": "Parse and validate a date string in YYYY-MM-DD format.",
        "path": "src/validation/date.py",
    },
    {
        "id": 38,
        "fn": "validate_phone",
        "doc": "Validate an international phone number format using pattern matching.",
        "path": "src/validation/phone.py",
    },
    {
        "id": 39,
        "fn": "validate_input",
        "doc": "Sanitize and validate generic user input, stripping dangerous characters.",
        "path": "src/validation/sanitize.py",
    },
    # --- sorting / algorithms (40-44) ---
    {
        "id": 40,
        "fn": "sort_list",
        "doc": "Sort a list of items in ascending order using the built-in Timsort algorithm.",
        "path": "src/algorithms/sorting.py",
    },
    {
        "id": 41,
        "fn": "binary_search",
        "doc": "Search for a target value in a sorted list using binary search.",
        "path": "src/algorithms/search.py",
    },
    {
        "id": 42,
        "fn": "merge_sort",
        "doc": "Recursively split and merge a list into sorted order.",
        "path": "src/algorithms/sorting.py",
    },
    {
        "id": 43,
        "fn": "quicksort",
        "doc": "In-place quicksort implementation using Lomuto partition scheme.",
        "path": "src/algorithms/sorting.py",
    },
    {
        "id": 44,
        "fn": "heap_sort",
        "doc": "Sort a list using a max-heap data structure in O(n log n) time.",
        "path": "src/algorithms/sorting.py",
    },
    # --- crypto / hashing (45-49) ---
    {
        "id": 45,
        "fn": "calculate_hash",
        "doc": "Calculate the SHA-256 hash of a byte string and return as hex digest.",
        "path": "src/crypto/hashing.py",
    },
    {
        "id": 46,
        "fn": "encrypt_data",
        "doc": "Encrypt bytes using AES-256-GCM symmetric encryption with a provided key.",
        "path": "src/crypto/encryption.py",
    },
    {
        "id": 47,
        "fn": "decrypt_data",
        "doc": "Decrypt AES-256-GCM ciphertext using the corresponding symmetric key.",
        "path": "src/crypto/encryption.py",
    },
    {
        "id": 48,
        "fn": "sign_message",
        "doc": "Produce an HMAC-SHA256 signature for a message using a secret key.",
        "path": "src/crypto/signing.py",
    },
    {
        "id": 49,
        "fn": "verify_signature",
        "doc": "Verify an HMAC-SHA256 message signature against the expected digest.",
        "path": "src/crypto/signing.py",
    },
]

# ---------------------------------------------------------------------------
# Queries: (label, query_text, relevant_ids set)
# ---------------------------------------------------------------------------

_QUERIES: list[tuple[str, str, set[int]]] = [
    ("database connection", "database connection", {5, 6, 7, 8, 9}),
    ("user authentication", "user authentication", {0, 1, 2, 3, 4}),
    ("parse JSON", "parse JSON", {20, 21, 22, 23, 24}),
    ("read file", "read file contents", {10, 11, 12, 13, 14}),
    ("HTTP POST", "HTTP POST request", {15, 16, 17, 18, 19}),
    ("log error", "log error message", {25, 26, 27, 28, 29}),
    ("async coroutine", "async coroutine", {30, 31, 32, 33, 34}),
    ("validate email", "validate email address", {35, 36, 37, 38, 39}),
    ("sort list", "sort list items", {40, 41, 42, 43, 44}),
    ("calculate hash", "calculate hash encrypt", {45, 46, 47, 48, 49}),
]


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------


def _precision_at_k(retrieved: list[int], relevant: set[int], k: int = 5) -> float:
    top = retrieved[:k]
    return sum(1 for r in top if r in relevant) / k if k else 0.0


def _recall_at_k(retrieved: list[int], relevant: set[int], k: int = 5) -> float:
    top = retrieved[:k]
    return sum(1 for r in top if r in relevant) / len(relevant) if relevant else 0.0


def _mrr(retrieved: list[int], relevant: set[int]) -> float:
    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in relevant:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# Approach 1: FTS5 trigram
# ---------------------------------------------------------------------------


class Fts5TrigramApproach:
    name = "FTS5-trigram"
    available = True

    def __init__(self) -> None:
        self._conn: sqlite3.Connection | None = None

    def setup(self, corpus: list[dict]) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE code_fts USING fts5(entry_id UNINDEXED, content, tokenize='trigram')")
        conn.executemany(
            "INSERT INTO code_fts(entry_id, content) VALUES (?, ?)",
            [(e["id"], f"{e['fn']} {e['doc']} {e['path']}") for e in corpus],
        )
        conn.commit()
        self._conn = conn

    def search(self, query: str) -> list[int]:
        safe_q = re.sub(r'["\']', "", query)
        rows = self._conn.execute(  # type: ignore[union-attr]
            "SELECT entry_id FROM code_fts WHERE code_fts MATCH ? ORDER BY rank LIMIT 20",
            (safe_q,),
        ).fetchall()
        return [int(r[0]) for r in rows]

    def teardown(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# Approach 2: BM25 + manual tokenizer
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Whitespace+punctuation tokenizer with lowercase normalization."""
    tokens = re.split(r"[\s\-_./\\]+", text.lower())
    return [t for t in tokens if len(t) > 1]


class Bm25Approach:
    name = "BM25"
    available = True
    _K1 = 1.5
    _B = 0.75

    def __init__(self) -> None:
        self._docs: list[tuple[int, list[str]]] = []
        self._idf: dict[str, float] = {}
        self._avgdl: float = 0.0

    def setup(self, corpus: list[dict]) -> None:
        self._docs = [(e["id"], _tokenize(f"{e['fn']} {e['doc']} {e['path']}")) for e in corpus]
        n = len(self._docs)
        self._avgdl = sum(len(tokens) for _, tokens in self._docs) / n if n else 1.0
        df: dict[str, int] = {}
        for _, tokens in self._docs:
            for t in set(tokens):
                df[t] = df.get(t, 0) + 1
        self._idf = {t: math.log((n - freq + 0.5) / (freq + 0.5) + 1) for t, freq in df.items()}

    def search(self, query: str) -> list[int]:
        q_tokens = _tokenize(query)
        scores: list[tuple[float, int]] = []
        for doc_id, tokens in self._docs:
            dl = len(tokens)
            score = 0.0
            for qt in q_tokens:
                tf = tokens.count(qt)
                idf = self._idf.get(qt, 0.0)
                denom = tf + self._K1 * (1 - self._B + self._B * dl / self._avgdl)
                score += idf * (tf * (self._K1 + 1)) / denom if denom else 0.0
            if score > 0:
                scores.append((score, doc_id))
        scores.sort(reverse=True)
        return [doc_id for _, doc_id in scores[:20]]

    def teardown(self) -> None:
        self._docs = []
        self._idf = {}


# ---------------------------------------------------------------------------
# Approach 3: sqlite-vec (skipped gracefully if extension unavailable)
# ---------------------------------------------------------------------------


class SqliteVecApproach:
    name = "sqlite-vec"

    def __init__(self) -> None:
        self.available = False
        self._reason = "extension not loaded"
        self._conn: sqlite3.Connection | None = None

    def setup(self, corpus: list[dict]) -> None:
        try:
            import sqlite_vec  # type: ignore[import-not-found]

            conn = sqlite3.connect(":memory:")
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            dim = 4
            conn.execute(f"CREATE VIRTUAL TABLE vec_items USING vec0(embedding float[{dim}])")
            # Stub: zero-vectors (real use would call an embedding model)
            conn.executemany(
                "INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)",
                [(e["id"], str([0.0] * dim)) for e in corpus],
            )
            conn.commit()
            self._conn = conn
            self.available = True
        except Exception as exc:
            self.available = False
            self._reason = str(exc)

    def search(self, query: str) -> list[int]:  # noqa: ARG002
        if not self.available or self._conn is None:
            return []
        rows = self._conn.execute("SELECT rowid FROM vec_items LIMIT 20").fetchall()
        return [int(r[0]) for r in rows]

    def teardown(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# Approach 4: ripgrep subprocess
# ---------------------------------------------------------------------------


class RipgrepApproach:
    name = "ripgrep"

    def __init__(self) -> None:
        self._rg_bin = shutil.which("rg")
        self.available = self._rg_bin is not None
        self._reason = "rg not on PATH" if not self.available else ""
        self._tmpdir: str | None = None
        self._id_map: dict[str, int] = {}

    def setup(self, corpus: list[dict]) -> None:
        if not self.available:
            return
        self._tmpdir = tempfile.mkdtemp(prefix="sk_bench_rg_")
        for e in corpus:
            fname = f"entry_{e['id']:03d}.py"
            fpath = os.path.join(self._tmpdir, fname)
            self._id_map[fname] = e["id"]
            content = f'def {e["fn"]}():\n    """{e["doc"]}"""\n    pass\n'
            Path(fpath).write_text(content, encoding="utf-8")

    def search(self, query: str) -> list[int]:
        if not self.available or not self._tmpdir:
            return []
        term = query.split()[0]
        try:
            result = subprocess.run(
                [self._rg_bin, "-l", "--no-heading", "-i", term, self._tmpdir],
                capture_output=True,
                text=True,
                timeout=5,
            )
            found = []
            for line in result.stdout.splitlines():
                fname = os.path.basename(line.strip())
                if fname in self._id_map:
                    found.append(self._id_map[fname])
            return found[:20]
        except Exception:
            return []

    def teardown(self) -> None:
        if self._tmpdir and os.path.isdir(self._tmpdir):
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._tmpdir = None


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def _run_approach(
    approach,
    corpus: list[dict],
    queries: list[tuple[str, str, set[int]]],
    *,
    profile: bool,
) -> dict:
    approach.setup(corpus)
    if not approach.available:
        approach.teardown()
        return {
            "name": approach.name,
            "available": False,
            "reason": getattr(approach, "_reason", "unavailable"),
        }

    latencies: list[float] = []
    p5_scores: list[float] = []
    r5_scores: list[float] = []
    mrr_scores: list[float] = []

    for _label, q_text, relevant in queries:
        t0 = time.perf_counter()
        retrieved = approach.search(q_text)
        latencies.append((time.perf_counter() - t0) * 1000)
        if profile:
            p5_scores.append(_precision_at_k(retrieved, relevant))
            r5_scores.append(_recall_at_k(retrieved, relevant))
            mrr_scores.append(_mrr(retrieved, relevant))

    approach.teardown()

    result: dict = {"name": approach.name, "available": True}
    if latencies:
        result["latency_avg_ms"] = sum(latencies) / len(latencies)
        result["latency_p50_ms"] = sorted(latencies)[len(latencies) // 2]
    if profile and p5_scores:
        result["precision_at_5"] = sum(p5_scores) / len(p5_scores)
        result["recall_at_5"] = sum(r5_scores) / len(r5_scores)
        result["mrr"] = sum(mrr_scores) / len(mrr_scores)
    return result


def run_benchmark(*, profile: bool = False) -> list[dict]:
    corpus = _CORPUS
    queries = _QUERIES if profile else _QUERIES[:1]  # smoke: 1 query

    approaches = [
        Fts5TrigramApproach(),
        Bm25Approach(),
        SqliteVecApproach(),
        RipgrepApproach(),
    ]
    return [_run_approach(ap, corpus, queries, profile=profile) for ap in approaches]


def _print_markdown_table(results: list[dict]) -> None:
    print("\n## Code-Search Benchmark Results\n")
    print("| Approach | Avail | Latency avg (ms) | P@5 | R@5 | MRR |")
    print("|----------|-------|------------------|-----|-----|-----|")
    for r in results:
        avail = "✅" if r.get("available") else f"⏭ {r.get('reason', '')}"
        lat = f"{r['latency_avg_ms']:.2f}" if "latency_avg_ms" in r else "—"
        p5 = f"{r['precision_at_5']:.3f}" if "precision_at_5" in r else "—"
        r5 = f"{r['recall_at_5']:.3f}" if "recall_at_5" in r else "—"
        mrr = f"{r['mrr']:.3f}" if "mrr" in r else "—"
        print(f"| {r['name']:<8} | {avail} | {lat} | {p5} | {r5} | {mrr} |")
    print()


# ---------------------------------------------------------------------------
# unittest smoke tests (CI gate)
# ---------------------------------------------------------------------------


class TestCodeSearchBenchmarkSmoke(unittest.TestCase):
    """Fast smoke tests — all approaches complete without raising exceptions."""

    @classmethod
    def setUpClass(cls):
        cls.results = run_benchmark(profile=False)

    def test_fts5_trigram_runs(self):
        r = next(x for x in self.results if x["name"] == "FTS5-trigram")
        self.assertTrue(r.get("available"), "FTS5-trigram should always be available")
        self.assertIn("latency_avg_ms", r)

    def test_bm25_runs(self):
        r = next(x for x in self.results if x["name"] == "BM25")
        self.assertTrue(r.get("available"), "BM25 should always be available")
        self.assertIn("latency_avg_ms", r)

    def test_sqlite_vec_completes_without_error(self):
        r = next(x for x in self.results if x["name"] == "sqlite-vec")
        self.assertIn("available", r)

    def test_ripgrep_completes_without_error(self):
        r = next(x for x in self.results if x["name"] == "ripgrep")
        self.assertIn("available", r)

    def test_all_four_approaches_present(self):
        names = {r["name"] for r in self.results}
        self.assertEqual(names, {"FTS5-trigram", "BM25", "sqlite-vec", "ripgrep"})

    def test_fts5_latency_under_100ms(self):
        r = next(x for x in self.results if x["name"] == "FTS5-trigram")
        if r.get("available"):
            self.assertLess(r["latency_avg_ms"], 100, "FTS5 trigram unexpectedly slow")

    def test_bm25_latency_under_100ms(self):
        r = next(x for x in self.results if x["name"] == "BM25")
        if r.get("available"):
            self.assertLess(r["latency_avg_ms"], 100, "BM25 unexpectedly slow")


# ---------------------------------------------------------------------------
# BM25 unit tests
# ---------------------------------------------------------------------------


class TestBm25(unittest.TestCase):
    def setUp(self):
        self.ap = Bm25Approach()
        self.ap.setup(_CORPUS)

    def tearDown(self):
        self.ap.teardown()

    def test_returns_list(self):
        self.assertIsInstance(self.ap.search("database connection"), list)

    def test_database_query_ranks_db_entries_high(self):
        top5 = set(self.ap.search("database connection")[:5])
        self.assertTrue(top5 & {5, 6, 7, 8, 9}, f"Expected DB entries in top-5, got {top5}")

    def test_auth_query_finds_auth_entries(self):
        top5 = set(self.ap.search("user authentication")[:5])
        self.assertTrue(top5 & {0, 1, 2, 3, 4}, f"Expected auth entries in top-5, got {top5}")

    def test_empty_query_returns_list(self):
        self.assertIsInstance(self.ap.search(""), list)


# ---------------------------------------------------------------------------
# FTS5 unit tests
# ---------------------------------------------------------------------------


class TestFts5Trigram(unittest.TestCase):
    def setUp(self):
        self.ap = Fts5TrigramApproach()
        self.ap.setup(_CORPUS)

    def tearDown(self):
        self.ap.teardown()

    def test_returns_list(self):
        self.assertIsInstance(self.ap.search("database"), list)

    def test_file_query_finds_results(self):
        result = self.ap.search("read file")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_search_returns_integer_ids(self):
        for r in self.ap.search("sort list"):
            self.assertIsInstance(r, int)


# ---------------------------------------------------------------------------
# Metrics unit tests
# ---------------------------------------------------------------------------


class TestMetrics(unittest.TestCase):
    def test_precision_perfect(self):
        self.assertAlmostEqual(_precision_at_k([1, 2, 3, 4, 5], {1, 2, 3, 4, 5}), 1.0)

    def test_precision_zero(self):
        self.assertAlmostEqual(_precision_at_k([10, 11, 12, 13, 14], {1, 2, 3, 4, 5}), 0.0)

    def test_recall_perfect(self):
        self.assertAlmostEqual(_recall_at_k([1, 2, 3, 4, 5], {1, 2, 3, 4, 5}), 1.0)

    def test_recall_partial(self):
        self.assertAlmostEqual(_recall_at_k([1, 2, 99, 98, 97], {1, 2, 3, 4, 5}), 0.4)

    def test_mrr_first_hit(self):
        self.assertAlmostEqual(_mrr([5, 1, 2], {5}), 1.0)

    def test_mrr_second_hit(self):
        self.assertAlmostEqual(_mrr([99, 5, 2], {5}), 0.5)

    def test_mrr_no_hit(self):
        self.assertAlmostEqual(_mrr([10, 11, 12], {5}), 0.0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    profile = "--profile" in sys.argv

    if profile:
        print("Running full code-search benchmark (--profile)…")
        results = run_benchmark(profile=True)
        _print_markdown_table(results)
        skippable = {"sqlite-vec", "ripgrep"}
        all_ok = all(r.get("available") or r["name"] in skippable for r in results)
        return 0 if all_ok else 1

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestCodeSearchBenchmarkSmoke, TestBm25, TestFts5Trigram, TestMetrics):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
