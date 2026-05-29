# Session Checkpoint: batch-ingest feature (issue #576)

This checkpoint documents key decisions, patterns, and mistakes from the batch ingest work.

## Decision

Use `sha256(source_path + ":" + heading_slug)[:12]` as the stable_id for all batch-ingested entries. This gives us a deterministic 12-character key that survives re-ingestion of the same file without creating duplicate rows. The stable_id is stored in the existing `stable_id` column of `knowledge_entries` so the existing sync infrastructure picks it up automatically.

## Pattern

Parse markdown files by splitting on `## Heading` boundaries, then map the heading text to a knowledge category. This lets checkpoint files and PR bodies both serve as structured knowledge sources without requiring any special markup beyond standard markdown headings.

## Mistake

Tried to reuse the `import_from_file` function for checkpoint ingest, but its format requires `## category: Title` which no real checkpoint or PR body uses. The batch ingest path needs its own parser that treats the heading itself as both the title and the category signal.

## Technical Details

The idempotency check queries `knowledge_entries WHERE stable_id = ?` before each insertion. If the stable_id already exists, the section is skipped silently. After a successful `add_entry()` call, the stable_id is overwritten from the default session-based value to the batch-derived value via a follow-up UPDATE.

## Next Steps

Wire `--from-checkpoint` into the `sk tentacle handoff` flow so handoff.md files are auto-ingested. Add `--from-pr` support to the post-merge hook so merged PRs seed the knowledge base automatically.
