## Summary

<!-- What changed and why? Link the tracked issue with Closes/Fixes when applicable. -->

## Evidence Checklist

> See [docs/AGENT-RULES.md — Quality Checklist](../docs/AGENT-RULES.md#quality-checklist) for the full surface-by-surface verification guide.

Mark each item with command output or `N/A - <reason>`.

- [ ] Security tests: `python test_security.py`
- [ ] Fix/regression tests: `python test_fixes.py`
- [ ] Full Python suite: `python run_all_tests.py`
- [ ] Rollback runbook tests: `python tests/test_rollback_runbook.py`
- [ ] Migration rehearsal: `python tests/test_migration_rehearsal.py`
- [ ] Install sandbox: `python tests/test_install_sandbox.py`
- [ ] Hook compatibility/security: `python tests/test_hook_compat.py` and `python tests/test_quality_gates.py`
- [ ] Cross-platform CI: Linux, macOS, and Windows checks are passing or explicitly not applicable
- [ ] Migration/DB evidence: required for `migrate.py` or schema changes, otherwise `N/A`
- [ ] Installer/update evidence: required for installer, updater, or release-install changes, otherwise `N/A`
- [ ] Hook evidence: required for hook provisioning or hook-rule changes, otherwise `N/A`

