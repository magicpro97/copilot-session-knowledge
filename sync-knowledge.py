#!/usr/bin/env python3
"""
sync-knowledge.py — Merge multiple knowledge.db files across environments

Synchronizes knowledge databases between Windows, WSL, and other machines.
Uses SQLite's ATTACH DATABASE to merge with INSERT OR IGNORE dedup.

Usage:
    python sync-knowledge.py --sources /path/to/other/knowledge.db  # Merge from source
    python sync-knowledge.py --sources db1.db db2.db                # Merge multiple
    python sync-knowledge.py --dry-run --sources /path/to/other.db  # Preview only
    python sync-knowledge.py --stats                                # Show sync info
    python sync-knowledge.py --auto                                 # Auto-detect WSL/Win DBs

Cross-platform: Windows, macOS, Linux (WSL). Pure Python stdlib.
"""

import sqlite3
import os
import sys
import shutil
from pathlib import Path
from datetime import datetime

# Fix Windows console encoding
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"


def auto_detect_sources() -> list[Path]:
    """Auto-detect knowledge.db files from known environments."""
    candidates = []

    # WSL paths accessible from Windows
    if os.name == "nt":
        # Try common WSL distro paths
        wsl_paths = [
            Path(r"\\wsl$\Ubuntu\home") / os.getlogin() / ".copilot" / "session-state" / "knowledge.db",
            Path(r"\\wsl$\Ubuntu-22.04\home") / os.getlogin() / ".copilot" / "session-state" / "knowledge.db",
            Path(r"\\wsl$\Debian\home") / os.getlogin() / ".copilot" / "session-state" / "knowledge.db",
        ]
        # Also try to get actual WSL username
        try:
            import subprocess
            result = subprocess.run(
                ["wsl", "bash", "-c", "echo $HOME"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                wsl_home = result.stdout.strip()
                # Validate WSL home path: must start with /home/ and contain no traversal
                if (wsl_home.startswith("/home/") and ".." not in wsl_home
                        and "\n" not in wsl_home and len(wsl_home) < 256):
                    wsl_db = Path(r"\\wsl$\Ubuntu" + wsl_home) / ".copilot" / "session-state" / "knowledge.db"
                    candidates.append(wsl_db)
                else:
                    print(f"⚠ Ignoring suspicious WSL home path: {wsl_home!r}", file=sys.stderr)
        except Exception as e:
            print(f"⚠ WSL detection failed: {e}", file=sys.stderr)
        candidates.extend(wsl_paths)

    # Windows paths accessible from WSL
    elif os.path.exists("/mnt/c"):
        # Try common Windows user paths
        for user_dir in Path("/mnt/c/Users").iterdir():
            if user_dir.is_dir() and user_dir.name not in ("Public", "Default", "All Users", "Default User"):
                win_db = user_dir / ".copilot" / "session-state" / "knowledge.db"
                candidates.append(win_db)

    # Filter: only existing files that aren't our own DB
    sources = []
    my_db = DB_PATH.resolve() if DB_PATH.exists() else DB_PATH
    for p in candidates:
        try:
            if p.exists() and p.resolve() != my_db:
                sources.append(p)
        except (OSError, PermissionError):
            continue

    return sources


def backup_db(db_path: Path) -> Path:
    """Create a WAL-safe backup of the database using sqlite3 backup API."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_suffix(f".backup_{timestamp}.db")
    # Use sqlite3 online backup API — safe even with WAL journal mode
    src_conn = sqlite3.connect(str(db_path))
    dst_conn = sqlite3.connect(str(backup_path))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
    # Verify backup is readable
    verify_conn = sqlite3.connect(str(backup_path))
    try:
        verify_conn.execute("PRAGMA quick_check")
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise RuntimeError(f"Backup verification failed: integrity check failed for {db_path}")
    finally:
        verify_conn.close()
    return backup_path


def get_db_stats(db_path: Path) -> dict:
    """Get statistics from a knowledge.db file."""
    stats = {"path": str(db_path), "size_kb": db_path.stat().st_size / 1024}
    db = sqlite3.connect(str(db_path))
    try:
        stats["sessions"] = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        stats["documents"] = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        stats["sections"] = db.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
        stats["knowledge_entries"] = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]

        # Check for source column
        cols = [c[1] for c in db.execute("PRAGMA table_info(sessions)").fetchall()]
        if "source" in cols:
            stats["has_source_col"] = True
            for row in db.execute("SELECT source, COUNT(*) FROM sessions GROUP BY source"):
                stats[f"sessions_{row[0]}"] = row[1]
        else:
            stats["has_source_col"] = False

        # Embeddings count
        try:
            stats["embeddings"] = db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        except sqlite3.OperationalError:
            stats["embeddings"] = 0

        # FTS entries
        try:
            stats["fts_entries"] = db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]
        except sqlite3.OperationalError:
            stats["fts_entries"] = 0

    except sqlite3.OperationalError as e:
        stats["error"] = str(e)
    finally:
        db.close()
    return stats


def sync_from_source(target_db: sqlite3.Connection, source_path: Path,
                     dry_run: bool = False) -> dict:
    """Merge data from source knowledge.db into target.

    Uses ATTACH DATABASE + INSERT OR IGNORE for safe dedup merge.
    Copies remote/UNC sources to temp first for SQLite compatibility.
    Returns: {sessions, documents, sections, knowledge_entries, embeddings} counts of new rows.
    """
    results = {"sessions": 0, "documents": 0, "sections": 0, "knowledge_entries": 0, "embeddings": 0}

    # SQLite can't ATTACH over UNC paths or network shares — copy to temp
    import tempfile
    temp_copy = None
    actual_path = source_path
    source_str = str(source_path)
    if source_str.startswith("\\\\") or source_str.startswith("//") or "wsl$" in source_str.lower():
        # P1-9: create temp adjacent to target DB (same filesystem — no cross-device error)
        tmp_dir = DB_PATH.parent
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(suffix=".db", prefix="sync_src_", dir=str(tmp_dir))
        os.close(fd)
        temp_copy = Path(tmp_name)
        print(f"    Copying to temp (UNC path)...")
        shutil.copy2(source_path, temp_copy)
        actual_path = temp_copy

    # Attach source database
    # SECURITY NOTE: source_alias is a hardcoded constant, never from user input.
    # SQLite requires table-qualifier syntax for ATTACH'd databases which cannot use ? params.
    source_alias = "src"
    try:
        target_db.execute(f"ATTACH DATABASE ? AS {source_alias}", (str(actual_path),))

        # Check source schema has necessary tables
        src_tables = [r[0] for r in target_db.execute(
            f"SELECT name FROM {source_alias}.sqlite_master WHERE type='table'"
        ).fetchall()]

        if "sessions" not in src_tables:
            print(f"  Warning: source has no 'sessions' table, skipping")
            return results

        # Check if source has 'source' column
        src_cols = {c[1] for c in target_db.execute(
            f"PRAGMA {source_alias}.table_info(sessions)"
        ).fetchall()}
        has_source = "source" in src_cols

        # 1. Sync sessions
        if dry_run:
            results["sessions"] = target_db.execute(f"""
                SELECT COUNT(*) FROM {source_alias}.sessions s
                WHERE s.id NOT IN (SELECT id FROM sessions)
            """).fetchone()[0]
        else:
            if has_source:
                target_db.execute(f"""
                    INSERT OR IGNORE INTO sessions
                    (id, path, summary, total_checkpoints, total_research, total_files, has_plan, source, indexed_at)
                    SELECT id, path, summary, total_checkpoints, total_research, total_files, has_plan,
                           COALESCE(source, 'copilot'), indexed_at
                    FROM {source_alias}.sessions
                """)
            else:
                target_db.execute(f"""
                    INSERT OR IGNORE INTO sessions
                    (id, path, summary, total_checkpoints, total_research, total_files, has_plan, source, indexed_at)
                    SELECT id, path, summary, total_checkpoints, total_research, total_files, has_plan,
                           'copilot', indexed_at
                    FROM {source_alias}.sessions
                """)
            results["sessions"] = target_db.execute("SELECT changes()").fetchone()[0]

        # 2. Sync documents (dedup by file_path UNIQUE)
        if "documents" in src_tables:
            src_doc_cols = {c[1] for c in target_db.execute(
                f"PRAGMA {source_alias}.table_info(documents)"
            ).fetchall()}
            has_doc_source = "source" in src_doc_cols

            if dry_run:
                results["documents"] = target_db.execute(f"""
                    SELECT COUNT(*) FROM {source_alias}.documents d
                    WHERE d.file_path NOT IN (SELECT file_path FROM documents)
                """).fetchone()[0]
            else:
                if has_doc_source:
                    target_db.execute(f"""
                        INSERT OR IGNORE INTO documents
                        (session_id, doc_type, seq, title, file_path, file_hash, size_bytes, content_preview, source, indexed_at)
                        SELECT session_id, doc_type, seq, title, file_path, file_hash, size_bytes, content_preview,
                               COALESCE(source, 'copilot'), indexed_at
                        FROM {source_alias}.documents
                    """)
                else:
                    target_db.execute(f"""
                        INSERT OR IGNORE INTO documents
                        (session_id, doc_type, seq, title, file_path, file_hash, size_bytes, content_preview, source, indexed_at)
                        SELECT session_id, doc_type, seq, title, file_path, file_hash, size_bytes, content_preview,
                               'copilot', indexed_at
                        FROM {source_alias}.documents
                    """)
                results["documents"] = target_db.execute("SELECT changes()").fetchone()[0]

        # 3. Sync sections (need to map document IDs)
        # We can only sync sections for documents that were successfully copied
        if "sections" in src_tables and not dry_run:
            # Get newly synced documents by matching file_path
            cursor = target_db.execute(f"""
                SELECT t.id as target_id, s.id as source_id
                FROM documents t
                JOIN {source_alias}.documents s ON t.file_path = s.file_path
            """)
            doc_id_map = {row[1]: row[0] for row in cursor.fetchall()}

            section_count = 0
            for src_doc_id, tgt_doc_id in doc_id_map.items():
                src_sections = target_db.execute(f"""
                    SELECT section_name, content FROM {source_alias}.sections
                    WHERE document_id = ?
                """, (src_doc_id,)).fetchall()
                for section_name, content in src_sections:
                    try:
                        target_db.execute("""
                            INSERT OR IGNORE INTO sections (document_id, section_name, content)
                            VALUES (?, ?, ?)
                        """, (tgt_doc_id, section_name, content))
                        section_count += target_db.execute("SELECT changes()").fetchone()[0]
                    except sqlite3.IntegrityError:
                        pass
            results["sections"] = section_count
        elif dry_run:
            results["sections"] = target_db.execute(f"""
                SELECT COUNT(*) FROM {source_alias}.sections
            """).fetchone()[0]

        # 4. Sync knowledge_entries (dedup by category+title+session_id UNIQUE)
        if "knowledge_entries" in src_tables:
            src_ke_cols = {c[1] for c in target_db.execute(
                f"PRAGMA {source_alias}.table_info(knowledge_entries)"
            ).fetchall()}
            has_ke_source = "source" in src_ke_cols

            if dry_run:
                results["knowledge_entries"] = target_db.execute(f"""
                    SELECT COUNT(*) FROM {source_alias}.knowledge_entries ke
                    WHERE NOT EXISTS (
                        SELECT 1 FROM knowledge_entries t
                        WHERE t.category = ke.category
                          AND t.title = ke.title
                          AND t.session_id = ke.session_id
                    )
                """).fetchone()[0]
            else:
                if has_ke_source:
                    target_db.execute(f"""
                        INSERT OR IGNORE INTO knowledge_entries
                        (session_id, document_id, category, title, content, tags, confidence,
                         occurrence_count, first_seen, last_seen, source)
                        SELECT session_id, document_id, category, title, content, tags, confidence,
                               occurrence_count, first_seen, last_seen, COALESCE(source, 'copilot')
                        FROM {source_alias}.knowledge_entries
                    """)
                else:
                    target_db.execute(f"""
                        INSERT OR IGNORE INTO knowledge_entries
                        (session_id, document_id, category, title, content, tags, confidence,
                         occurrence_count, first_seen, last_seen, source)
                        SELECT session_id, document_id, category, title, content, tags, confidence,
                               occurrence_count, first_seen, last_seen, 'copilot'
                        FROM {source_alias}.knowledge_entries
                    """)
                results["knowledge_entries"] = target_db.execute("SELECT changes()").fetchone()[0]

        # 5. Sync embeddings (if table exists in both)
        if "embeddings" in src_tables:
            try:
                target_db.execute("SELECT 1 FROM embeddings LIMIT 1")
                if dry_run:
                    results["embeddings"] = target_db.execute(f"""
                        SELECT COUNT(*) FROM {source_alias}.embeddings e
                        WHERE NOT EXISTS (
                            SELECT 1 FROM embeddings t
                            WHERE t.source_type = e.source_type
                              AND t.source_id = e.source_id
                        )
                    """).fetchone()[0]
                else:
                    target_db.execute(f"""
                        INSERT OR IGNORE INTO embeddings
                            (source_type, source_id, provider, model,
                             dimensions, vector, text_preview, created_at)
                        SELECT source_type, source_id, provider, model,
                               dimensions, vector, text_preview, created_at
                        FROM {source_alias}.embeddings
                    """)
                    results["embeddings"] = target_db.execute("SELECT changes()").fetchone()[0]
            except sqlite3.OperationalError:
                pass  # embeddings table doesn't exist in target

    finally:
        # Commit any pending operations before DETACH
        try:
            target_db.commit()
        except Exception as e:
            print(f"⚠ Merge error: {e}", file=sys.stderr)
        try:
            target_db.execute(f"DETACH DATABASE {source_alias}")
        except sqlite3.OperationalError:
            pass
        # Clean up temp copy
        if temp_copy and temp_copy.exists():
            try:
                temp_copy.unlink()
            except OSError:
                pass

    return results


def rebuild_fts(db: sqlite3.Connection):
    """Rebuild FTS5 indexes after merge."""
    print("  Rebuilding FTS indexes...")

    # Rebuild knowledge_fts
    db.execute("DELETE FROM knowledge_fts")
    db.execute("""
        INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id)
        SELECT d.title, s.section_name, s.content, d.doc_type, d.session_id, s.document_id
        FROM sections s
        JOIN documents d ON s.document_id = d.id
    """)
    fts_count = db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]
    print(f"    knowledge_fts: {fts_count} entries")

    # Rebuild ke_fts if knowledge_entries exist
    try:
        db.execute("DELETE FROM ke_fts")
        db.execute("""
            INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
            SELECT id, title, content, tags, category,
                   COALESCE(wing,''), COALESCE(room,''), COALESCE(facts,'[]')
            FROM knowledge_entries
        """)
        ke_count = db.execute("SELECT COUNT(*) FROM ke_fts").fetchone()[0]
        print(f"    ke_fts: {ke_count} entries")
    except sqlite3.OperationalError:
        pass


def show_stats():
    """Show sync info for all detectable DBs."""
    print(f"\n{'='*60}")
    print("  Knowledge DB Sync Info")
    print(f"{'='*60}")

    # Target DB
    if DB_PATH.exists():
        print(f"\n  Target (local): {DB_PATH}")
        stats = get_db_stats(DB_PATH)
        print(f"    Size: {stats['size_kb']:.1f} KB")
        print(f"    Sessions: {stats.get('sessions', '?')}")
        print(f"    Documents: {stats.get('documents', '?')}")
        print(f"    Knowledge: {stats.get('knowledge_entries', '?')}")
        print(f"    Embeddings: {stats.get('embeddings', '?')}")
        if stats.get("has_source_col"):
            for key, val in stats.items():
                if key.startswith("sessions_"):
                    src = key.replace("sessions_", "")
                    print(f"    Sessions ({src}): {val}")
    else:
        print(f"\n  Target: {DB_PATH} (not found)")

    # Source DBs
    sources = auto_detect_sources()
    if sources:
        print(f"\n  Detected sources:")
        for s in sources:
            stats = get_db_stats(s)
            print(f"\n    {s}")
            print(f"      Size: {stats['size_kb']:.1f} KB")
            print(f"      Sessions: {stats.get('sessions', '?')}")
            print(f"      Documents: {stats.get('documents', '?')}")
            print(f"      Knowledge: {stats.get('knowledge_entries', '?')}")
    else:
        print(f"\n  No remote sources auto-detected.")
        print(f"  Use --sources <path> to specify manually.")


def main():
    dry_run = "--dry-run" in sys.argv
    stats_only = "--stats" in sys.argv
    auto_mode = "--auto" in sys.argv
    source_paths = []

    if "--sources" in sys.argv:
        idx = sys.argv.index("--sources")
        for i in range(idx + 1, len(sys.argv)):
            if sys.argv[i].startswith("--"):
                break
            source_paths.append(Path(sys.argv[i]))

    if stats_only:
        show_stats()
        return

    if auto_mode:
        source_paths = auto_detect_sources()

    if not source_paths:
        print("No source databases specified.")
        print("Use --sources <path> or --auto to detect.")
        print("Use --stats to see available databases.")
        return

    # Validate sources
    valid_sources = []
    for p in source_paths:
        if p.exists():
            valid_sources.append(p)
        else:
            print(f"  Warning: {p} not found, skipping")

    if not valid_sources:
        print("No valid source databases found.")
        return

    # Ensure target DB exists with proper schema
    sys.path.insert(0, str(Path(__file__).parent))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_session_index",
        Path(__file__).parent / "build-session-index.py"
    )
    bsi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bsi)

    # Backup before merge
    if DB_PATH.exists() and not dry_run:
        backup = backup_db(DB_PATH)
        print(f"Backup: {backup}")

    db = bsi.create_db(DB_PATH)
    db.execute("PRAGMA busy_timeout=5000")  # wait up to 5 s on concurrent writes

    mode = "DRY RUN" if dry_run else "SYNC"
    print(f"\n{mode}: Merging {len(valid_sources)} source(s) → {DB_PATH}")

    total = {"sessions": 0, "documents": 0, "sections": 0, "knowledge_entries": 0, "embeddings": 0}

    for source in valid_sources:
        print(f"\n  Source: {source}")
        src_stats = get_db_stats(source)
        print(f"    ({src_stats.get('sessions', '?')} sessions, {src_stats.get('knowledge_entries', '?')} knowledge entries)")

        results = sync_from_source(db, source, dry_run=dry_run)
        for k, v in results.items():
            total[k] += v
            if v > 0:
                print(f"    + {v} new {k}")

    if not dry_run:
        # Rebuild FTS indexes
        rebuild_fts(db)
        db.commit()

    print(f"\n{'='*40}")
    print(f"  Total {'(would sync)' if dry_run else 'synced'}:")
    for k, v in total.items():
        if v > 0:
            print(f"    {k}: +{v}")
    if sum(total.values()) == 0:
        print(f"    (no new data to sync)")
    print(f"{'='*40}")

    db.close()


if __name__ == "__main__":
    main()
