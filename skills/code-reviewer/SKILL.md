---
name: code-reviewer
description: >
  Skeptical, high signal-to-noise code review that surfaces only genuine bugs, security
  vulnerabilities, and logic errors — never style or formatting. Use when reviewing a PR,
  diff, or agent-produced code before merging. Trigger phrases: "review this code",
  "review my PR", "code review", "check for bugs", "security review", "audit the diff",
  "look for issues", "is this safe to merge".
---

# Code Reviewer

Perform a skeptical, high signal-to-noise code review. Surface only genuine issues: bugs,
security vulnerabilities, logic errors, contract violations, and design flaws that affect
correctness. Never comment on style, formatting, or naming unless the ambiguity causes an
actual bug.

## When to Use

- Before merging a PR or branch into a shared base
- After an agent completes implementation work that lacks test coverage
- When code touches security-sensitive surfaces (auth, file I/O, DB queries, network)
- User says "review this", "check for bugs", "audit the code", "review my PR", "is this safe"

## Why Signal Matters

A review with 20 style comments and 1 real bug teaches authors to ignore reviews.
Every comment you write costs the author's attention. Spend it only on things that:

- Could cause a crash or incorrect behavior
- Introduce a security vulnerability
- Violate an existing contract (API, interface, invariant)
- Create a logic error under some input the tests don't cover
- Introduce a correctness regression in an existing feature

## Process

### Phase 1: Orient

Before reviewing, understand the context — reviewing code you haven't fully read produces
false confidence.

1. Read any PR description, commit message, or task specification first
2. Identify the entry points: which functions are called by callers? What are the contracts?
3. Read the tests — they reveal intent and expose gaps
4. Use `grep`, `glob`, and `view` to read affected files in full, including callers

### Phase 2: Investigate

Work through the changed code systematically across four dimensions:

**Correctness**
- Trace every code path with concrete inputs: null, empty string, zero, negative, boundary max
- Check loop bounds: off-by-one, skipped last element, early exit that bypasses cleanup
- Check error paths: errors caught, propagated correctly, or silently swallowed?
- Check all return values: are failure modes detectable by the caller?

**Security**
- SQL/NoSQL injection: is user input ever interpolated into queries (not parameterized)?
- Path traversal: does any file path include user-controlled segments without sanitization?
- Authentication bypass: are auth checks skippable under any condition?
- Sensitive data: are secrets, PII, or session tokens logged or echoed in responses?
- Dependency confusion: are package names verified? Are lockfiles committed alongside changes?

**Contracts**
- Does the function do exactly what its name and documentation say — no hidden side effects?
- Are invariants maintained across all exit paths, including exceptions and early returns?
- Do callers use the changed API correctly? A changed signature may break distant callsites.

**Resource management**
- Are connections, file handles, locks, and channels always released — even on error paths?
- Are there goroutines, threads, or async tasks that can leak if the caller abandons the call?
- Is shared mutable state accessed without synchronization (race conditions)?

### Phase 3: Report

Structure findings by severity. Be specific: file, line, concrete input that triggers the
issue, and why it matters.

```markdown
## Code Review: <scope / PR title>

### 🔴 Critical (fix before merge)
- **[file.go:42]** Description — concrete path to failure and impact

### 🟡 High (fix soon after merge)
- **[file.go:88]** Description — why it matters

### 🟢 Low (optional, low-risk)
- **[file.go:15]** Description — rationale

### ✅ Areas reviewed with no issues
- <module or file> — what was checked
```

A clean report is valuable information. If a section was out of scope or unread, say so
explicitly rather than implying it was reviewed.

## What Not to Report

Omit findings that fall into these categories — they are noise:

| Category | Example | Why skip |
|----------|---------|----------|
| Style | Indentation, line length | Linter handles it |
| Naming | "I'd rename this to `x`" | Not a correctness issue |
| Refactoring | "This could be simplified" | Separate concern |
| Missing comments | "This function needs a docstring" | Not a bug |
| Best practices without correctness reason | "You should use X pattern" | Opinion, not fact |

If you find yourself writing "Consider renaming..." or "This could be cleaner...", stop.
That comment doesn't belong in a correctness review.

## Anti-Patterns

| Anti-Pattern | Why It Fails |
|-------------|-------------|
| Reporting style alongside bugs | Trains authors to ignore reviews |
| Vague findings ("this could be a bug") | Author can't reproduce or fix it |
| No line reference | Finding can't be located without rereading everything |
| Reviewing without reading callers | Misses contract violations |
| Claiming "looks good" without tracing paths | False confidence |

<example>
**Reviewing a Go HTTP handler that serves static files:**

```go
func serveFile(w http.ResponseWriter, r *http.Request) {
    path := r.URL.Query().Get("file")
    data, err := os.ReadFile("/var/app/static/" + path)
    if err != nil {
        http.Error(w, "not found", 404)
        return
    }
    w.Write(data)
}
```

**Review: serveFile handler**

### 🔴 Critical (fix before merge)

- **handler.go:3** Path traversal — `path` is user-controlled and concatenated directly into
  the filesystem path. A request with `?file=../../etc/passwd` reads `/etc/passwd`. Fix with
  `filepath.Clean` and a prefix check:
  ```go
  clean := filepath.Clean(filepath.Join("/var/app/static", path))
  if !strings.HasPrefix(clean, "/var/app/static/") {
      http.Error(w, "forbidden", 403)
      return
  }
  ```

### ✅ Areas reviewed with no issues
- Error handling on `os.ReadFile` — returns 404 correctly
- Response writer usage — `w.Write` handles partial writes silently, acceptable for static files
</example>

<example>
**Reviewing a Python database query:**

```python
def get_user(username):
    query = f"SELECT * FROM users WHERE username = '{username}'"
    return db.execute(query).fetchone()
```

**Review: get_user**

### 🔴 Critical (fix before merge)

- **db.py:2** SQL injection — `username` is interpolated directly into the query string.
  A value like `' OR '1'='1` returns all users; `'; DROP TABLE users; --` destroys the table.
  Fix: use parameterized queries:
  ```python
  return db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
  ```

### ✅ Areas reviewed with no issues
- Return value — `fetchone()` returns `None` on miss, which is a safe sentinel for callers
</example>
