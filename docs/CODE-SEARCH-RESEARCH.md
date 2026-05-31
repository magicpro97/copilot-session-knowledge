# CODE-SEARCH-RESEARCH.md — Code Search Benchmark Methodology

> **Status:** Benchmark harness implemented. Results placeholder below — run `sk code-search-benchmark` to populate with real data.

## Overview

This document describes the benchmark methodology used to evaluate four code-search approaches for potential integration into `sk semantic code search`. Results from this benchmark will inform the implementation decisions for issues #743 (sqlite-vec) and #744 (Rust tree-sitter indexer).

## Benchmark Harness

**File:** `tests/test_code_search_benchmark.py`

Run the full benchmark:

```bash
sk code-search-benchmark
# or directly:
python3 tests/test_code_search_benchmark.py --profile
```

Run the CI smoke test (< 5 s):

```bash
python3 tests/test_code_search_benchmark.py
```

## The 4 Approaches

### 1. FTS5 Trigram

SQLite's built-in FTS5 virtual table with the `trigram` tokenizer. Splits content into overlapping 3-character n-grams, enabling substring and fuzzy-prefix matching without a separate indexing step.

- **Pros:** Zero dependencies, in-process, excellent for substring/fuzzy search
- **Cons:** No semantic understanding; trigram index can be large for long docs
- **Implementation:** `Fts5TrigramApproach` in the benchmark file

### 2. BM25 + Manual Tokenizer

Keyword-based Okapi BM25 scoring implemented in pure Python. Tokenizes on whitespace and punctuation, computes IDF per term and TF-adjusted BM25 scores at query time.

- **Pros:** Zero dependencies, interpretable scores, fast on small corpora
- **Cons:** Vocabulary mismatch kills recall; no stemming; O(|corpus| × |query|) at search time
- **Implementation:** `Bm25Approach` + `_tokenize()` in the benchmark file

### 3. sqlite-vec

The [sqlite-vec](https://github.com/asg017/sqlite-vec) extension enables storing and querying dense float vectors inside SQLite. In production use, text would be embedded with a local model (e.g., `nomic-embed-text`) before insertion.

- **Pros:** Semantic similarity across vocabulary mismatches; integrates with existing SQLite DB
- **Cons:** Requires optional C extension; embedding quality determines retrieval quality; stub vectors in benchmark give random scores
- **Implementation:** `SqliteVecApproach` — skipped gracefully if extension unavailable

### 4. ripgrep Subprocess

Shell out to `rg` (ripgrep) for regex/literal file search. The benchmark writes each corpus entry to a temp file and uses `rg -l -i <term>` to find matches.

- **Pros:** Extremely fast for large codebases; supports full regex; no index build time
- **Cons:** External dependency; disk I/O overhead; no ranking; single-term search in current impl
- **Implementation:** `RipgrepApproach` — skipped gracefully if `rg` not on PATH

## Corpus

50 synthetic Python function entries across 10 categories (5 per category):

| Category | IDs | Example function |
|----------|-----|-----------------|
| auth/security | 0–4 | `authenticate_user`, `hash_password` |
| database | 5–9 | `connect_database`, `execute_query` |
| file I/O | 10–14 | `read_file`, `write_file` |
| HTTP/network | 15–19 | `send_request`, `post_json` |
| JSON utils | 20–24 | `parse_json`, `validate_schema` |
| logging | 25–29 | `log_error`, `setup_logger` |
| async ops | 30–34 | `async_fetch`, `gather_tasks` |
| validation | 35–39 | `validate_email`, `validate_url` |
| sorting/algorithms | 40–44 | `sort_list`, `merge_sort` |
| crypto/hashing | 45–49 | `calculate_hash`, `encrypt_data` |

## Queries and Ground Truth

10 queries with hand-labeled relevant sets (5 relevant documents per query):

| # | Query | Relevant IDs |
|---|-------|-------------|
| 1 | `database connection` | 5–9 |
| 2 | `user authentication` | 0–4 |
| 3 | `parse JSON` | 20–24 |
| 4 | `read file contents` | 10–14 |
| 5 | `HTTP POST request` | 15–19 |
| 6 | `log error message` | 25–29 |
| 7 | `async coroutine` | 30–34 |
| 8 | `validate email address` | 35–39 |
| 9 | `sort list items` | 40–44 |
| 10 | `calculate hash encrypt` | 45–49 |

## Metrics

| Metric | Definition |
|--------|-----------|
| **Latency avg (ms)** | Mean wall-clock time per query across all 10 queries |
| **Latency P50 (ms)** | Median query latency |
| **Precision@5** | Fraction of top-5 results that are relevant (averaged over queries) |
| **Recall@5** | Fraction of relevant docs appearing in top-5 (averaged over queries) |
| **MRR** | Mean Reciprocal Rank — 1/rank of the first relevant result (averaged over queries) |

## Results Placeholder

> Run `python3 tests/test_code_search_benchmark.py --profile` and paste output here.

| Approach | Avail | Latency avg (ms) | P@5 | R@5 | MRR |
|----------|-------|------------------|-----|-----|-----|
| FTS5-trigram | — | — | — | — | — |
| BM25 | — | — | — | — | — |
| sqlite-vec | — | — | — | — | — |
| ripgrep | — | — | — | — | — |

## Decision Criteria

After running the benchmark on real codebase data, select the approach(es) for `sk` based on:

1. **MRR ≥ 0.6** — first relevant result should appear in top-2 on average
2. **Latency avg < 50 ms** — sub-50 ms feels interactive for CLI use
3. **No mandatory external deps** — optional C extensions OK if graceful fallback exists
4. **Recall@5 ≥ 0.5** — at least half the relevant docs should surface in top-5

FTS5 trigram and BM25 are pure stdlib and will always pass the dependency criterion. sqlite-vec and ripgrep require availability checks and graceful degradation.

## Related Issues

- [#739](https://github.com/magicpro97/copilot-session-knowledge/issues/739) — this benchmark harness
- [#743](https://github.com/magicpro97/copilot-session-knowledge/issues/743) — sqlite-vec integration
- [#744](https://github.com/magicpro97/copilot-session-knowledge/issues/744) — Rust tree-sitter indexer
