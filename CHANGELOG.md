# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Comprehensive docs/ directory (USAGE, SKILLS, HOOKS, AUTO-UPDATE)
- CONTRIBUTING.md with development guidelines
- CHANGELOG.md (this file)
- FAQ section in README
- Badges (license, python, platform, tests, dependencies)
- Table of Contents in README
- Collision-renamed tentacle bundles now include `slug` in `manifest.json` and a `Slug:` header in `session-metadata.md`, so sub-agents can always resolve the correct invocation name after a directory collision rename.
- `_normalize_posix_home()` in `check_subagent_marker.py` now handles Cygwin-style `/cygdrive/<drive>/...` paths in addition to Git Bash `/c/...` and WSL `/mnt/c/...` forms.

### Changed
- Restructured README from 577 lines to ~280 lines (moved details to docs/)
- Added "Why?" section for first-time readers
- Added Quick Start section (3 commands from zero to working)
- Cross-repo isolation git-root comparison now uses `Path.resolve()` canonical paths (`_same_canonical_root()`), so repos reached through dotdot paths or symlink-equivalent representations are correctly identified as the same repo — prevents stranded or duplicate dispatched-subagent markers.

### Fixed
- `tentacle.py` Unicode console failures on Windows: added standard UTF-8 stdout/stderr reconfigure block (matching `briefing.py`/`install.py` pattern), eliminating `UnicodeEncodeError` for emoji and non-ASCII output.
- `test_karpathy_skill_rollout.py` Windows path assertion failures: two assertions now use `.as_posix()` for cross-platform path comparison.
- POSIX home normalization in `check_subagent_marker.py` now uses explicit backslash (`chr(92)`) instead of `os.sep` for separator replacement, so the function produces correct Windows paths even when the Python runtime reports a POSIX `os.sep`. This fixes a latent bug that could have surfaced if the code were ever exercised in a POSIX-hosted Windows-emulation layer.
- Section 17g tests in `test_hooks.py` now build expected Windows paths with `chr(92)` instead of literal backslash string comparisons, making assertions valid on both Windows and non-Windows hosts.

## [1.1.0] - 2026-04-18

### Added
- Unified hook runner architecture (1 process vs 11 per event)
- HMAC-signed markers for tamper-resistant counters
- Audit logging for all hook decisions (`~/.copilot/markers/audit.jsonl`)
- Dry-run mode (`HOOK_DRY_RUN=1`)
- Hook tamper protection with OS immutable flags
- SHA256 integrity manifest for hook files
- Bash bypass detection via `git status`

### Fixed
- Emoji detection on macOS (grep -P → Python regex)
- commit-gate iOS screenshot 30-min time constraint

### Changed
- Merged duplicate hook rules (tentacle enforce+suggest, track+test)
- Fail-open architecture — rule errors don't block the agent

## [1.0.0] - 2026-04-01

### Added
- Initial release
- SQLite FTS5 session indexing
- 7-type knowledge extraction (mistake, pattern, decision, tool, feature, refactor, discovery)
- Knowledge graph with auto-detected relations
- Palace concepts (wing/room) for hierarchical organization
- Semantic search with embedding API support (OpenAI, Fireworks, OpenRouter)
- TF-IDF fallback for offline semantic search
- Auto-update mechanism with smart diff pipeline
- LaunchAgent/systemd/Task Scheduler auto-start
- Claude Code adapter (JSONL → common format)
- Cross-environment sync (Windows ↔ WSL)
- Input validation and SQL injection prevention
- 74 tests (9 security + 65 functional)
