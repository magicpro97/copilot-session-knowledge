# tests/fixtures/debug-log/

## Synthetic provenance declaration

All files in this directory are **deterministic synthetic JSONL** derived from the event envelope
shapes documented in:

- `browse/core/operator_console.py` (`_parse_output_event`, `_raw_event`)
- VS Code `IDebugLogEntry` field names
- OTel `ReadableSpan` field names
- `docs/DEBUG-LOG-CONTRACT.md`

**No real Copilot CLI session data, user prompts, operator session outputs, home directory paths,
usernames, API keys, tokens, or private session contents are present in these files.**

Contributions that include real session data will be rejected in code review. All fixture values
use neutral placeholders such as `fixture-session-001`, `fixture-run-001`, synthetic span IDs,
and placeholder tool/message names.

---

## Fixture files

| File | Cases covered |
|---|---|
| `debug-log-sample.jsonl` | All required cases (see below) |

---

## Cases covered by `debug-log-sample.jsonl`

| Line | Case |
|---|---|
| 1 | `session_start` — parsed JSON event with timestamp and synthetic span_id |
| 2 | `turn_start` — parsed JSON event with timestamp |
| 3 | `llm_request` — parsed JSON event |
| 4 | `tool_call` start — `tool_name` populated, `duration_ms = null` (no completion yet) |
| 5 | `tool_call` complete — `tool_name` populated, `duration_ms` set, `status = "ok"` |
| 6 | `agent_response` — assistant message kind |
| 7 | `error` — error kind, `status = "error"`, `level = "error"` |
| 8 | `raw` — non-JSON output line |
| 9 | `session_start` with absent timestamp — `timestamp = null` |
| 10 | `generic` event with absent `span_id` — `span_id` is synthetic, `parent_span_id = null` |

---

## Synthetic span_id worked example

The span IDs in the fixtures were computed as:

```
span_id = sha1(f"{source}:{idx}:{monotonic_seq}".encode("utf-8")).hexdigest()[:16]
```

where `monotonic_seq` starts at 1 and `idx` is the entry's own zero-based index.

Example (line 1, idx=0):
```
sha1("operator_console:0:1") -> 297c86d9d2c468a3...
span_id = "297c86d9d2c468a3"
```

**Exception — paired tool-call completion (lines 4–5, idx=3 and idx=4):**
The tool-call completion row (idx=4) reuses the span_id of the paired start row (idx=3) rather
than computing a new one from its own `idx`. This matches OTel semantics where a single span
covers the full start-to-complete interval. The formula is **not** applied to the completion
row's own `idx` in this case.

```
# Tool start (idx=3): sha1("operator_console:3:1")[:16] = "769d862309893e46"
# Tool complete (idx=4): reuses "769d862309893e46" (paired with idx=3)
```

See `docs/DEBUG-LOG-CONTRACT.md#synthetic-span-id-rule` for the full algorithm and the
paired-tool-call-completion carve-out.

---

## Verifying span_ids

To verify all non-paired synthetic span IDs match the formula, and the paired completion reuses
the start span, run acceptance command #8 from `docs/DEBUG-LOG-CONTRACT.md`:

```bash
python -c "
import json, hashlib

def formula(source, idx, seq=1):
    h = hashlib.sha1(f'{source}:{idx}:{seq}'.encode()).hexdigest()[:16]
    return h if h != '0000000000000000' else formula(source, idx, seq+1)

entries = [json.loads(l) for l in open('tests/fixtures/debug-log/debug-log-sample.jsonl') if l.strip()]
paired_completions = {4: 3}
span_by_idx = {e['idx']: e['span_id'] for e in entries}
errors = []
for e in entries:
    idx = e['idx']
    sid = e['span_id']
    if sid is None:
        continue
    if idx in paired_completions:
        expected = span_by_idx[paired_completions[idx]]
        if sid != expected:
            errors.append(f'idx={idx}: paired completion span {sid!r} != start span {expected!r}')
    else:
        expected = formula(e['source'], idx)
        if sid != expected:
            errors.append(f'idx={idx}: span {sid!r} != formula {expected!r}')
if errors:
    for err in errors: print('FAIL:', err)
    raise SystemExit(1)
print('OK: all synthetic span_ids consistent with formula and paired-tool-call reuse rule')
"
```

---

## Adding new fixtures

1. Use only synthetic placeholder values.
2. Compute span IDs using the formula above (note the paired tool-call exception for `tool_result` rows).
3. Verify with: `python -c "import json; [json.loads(l) for l in open('tests/fixtures/debug-log/debug-log-sample.jsonl') if l.strip()]"`
4. Check for real data: `grep -rn "\.copilot\|USERPROFILE\|/home/" tests/fixtures/debug-log/`
5. Run the span consistency check above to confirm all span IDs are correct.
