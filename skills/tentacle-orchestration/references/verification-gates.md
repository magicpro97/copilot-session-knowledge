# Verification Gates

Every tentacle's output goes through these gates before shipping.

## Gate Summary

| Gate | What it catches | Skip when |
|------|----------------|-----------|
| **Build** | Syntax errors, type mismatches, import failures | Never skip |
| **Lint** | Style violations, unused imports, formatting | Never skip |
| **Test** | Logic bugs, regressions, broken contracts | Never skip |
| **Review** | Security issues, design flaws, scope creep | Never skip |
| **Docs** | Stale README, outdated JSDoc, missing CHANGELOG | Internal refactors only |
| **QA audit** | Hallucinated tests, spec mismatches, blind spots | Low-risk changes only |

The first 4 gates are mandatory. Skipping any of them means you don't know if the agent output is correct — you're just hoping it is.

## Gate Details

### Build Gate

Run the project's compiler on all changed files. Do not trust agent claims that "it compiles."

```bash
# Examples — use whatever your project uses:
npx tsc --noEmit                    # TypeScript
cargo check                         # Rust
go build ./...                      # Go
python -m py_compile <file>         # Python
```

If build fails → fix before proceeding. Either fix yourself or re-dispatch the responsible tentacle agent with the error output.

### Lint Gate

Run the project's linter and formatter. Agents frequently produce code that compiles but violates project style rules (unused imports, missing JSDoc, inconsistent formatting).

```bash
# Examples — use whatever your project uses:
npx eslint <changed-files>          # JavaScript/TypeScript
npx prettier --check <changed-files>
cargo clippy                        # Rust
golangci-lint run                   # Go
ruff check <changed-files>          # Python
```

If lint fails → fix before proceeding. Most lint issues are auto-fixable (`--fix`), so fix them directly rather than re-dispatching an agent.

### Test Gate

Run actual tests. Agents often claim "all tests pass" without running them, or write tests that don't actually assert anything meaningful.

```bash
# Run tests for the affected modules
yarn test <changed-files> --maxWorkers=1
pytest <changed-files>
go test ./...
```

If tests fail → fix before proceeding. Check whether the agent wrote the tests — agents sometimes write tests that are trivially correct (e.g., testing that `true === true`).

### Code Review Gate

Dispatch a code-review agent (in a separate context — never let code review itself) to review all changes across tentacles.

```python
task(
    agent_type="code-review",
    model="claude-sonnet-4.6",
    prompt="Review all files changed by tentacle agents: <file list>. "
           "Focus on: correctness, security, scope violations, and missed edge cases."
)
```

If review finds issues → fix → re-review → loop until verdict is CLEAN (max 5 rounds).

### Docs Gate

Check whether changed code has documentation that needs updating. Agents change function signatures, add parameters, alter behavior — but almost never update the corresponding docs.

Look for:
- **README / docs/** — Do feature descriptions still match the code?
- **API docs** (OpenAPI, Swagger) — Do endpoints, parameters, and response schemas reflect changes?
- **JSDoc / docstrings** — Do changed functions have accurate descriptions?
- **CHANGELOG** — Does it mention the change (if project uses one)?
- **Inline comments** — Do comments near changed code still describe what the code does?

If docs are stale → update them directly rather than re-dispatching an agent.

Skip when changes are purely internal refactors with no public API or behavior change.

### QA Audit Gate (High-Risk Only)

For changes touching auth, data integrity, financial logic, or infrastructure, add a cross-check by a different agent. This catches errors that code-review misses because the reviewer may share the same blind spots as the author.

Skip this step for low-risk changes (documentation, formatting, simple refactors).
