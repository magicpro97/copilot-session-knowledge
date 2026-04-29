# Operator Actions Contract

> **Scope**: `docs/design/browse-ui/`  
> **Audience**: contributors extending browse diagnostics routes or frontend settings

## Overview

Browse diagnostics routes (`/api/sync/status`, `/api/scout/status`, `/api/tentacles/status`, `/api/skills/metrics`) each include an `operator_actions` array in their JSON response. These actions are **display-only, copy-safe command suggestions** — the browser never executes them.

## Shared Contract

Every operator action in every route must satisfy:

```json
{
  "id":          "<string>",         // unique within route
  "title":       "<string>",         // short human label
  "description": "<string>",         // one-sentence purpose
  "command":     "<non-empty string>", // the CLI command to copy
  "safe":        true                 // ALWAYS true — enforced by backend + frontend schema
}
```

### Optional context fields

These fields are route-specific and may be absent:

| Field | Route | Meaning |
|---|---|---|
| `requires_configured_gateway` | `sync.py` | Action needs a configured sync gateway URL |
| `requires_configured_target` | `scout.py` | Action needs a configured trend-scout target repo |

The frontend schema (`operatorActionSchema`) treats both as optional (`boolean?`). Do not add new required fields without updating all 4 routes, the Python module, and the TypeScript schema together.

## Backend Implementation

All 4 routes build their `operator_actions` lists using `browse.core.operator_actions.make_action()`:

```python
from browse.core.operator_actions import make_action

operator_actions = [
    make_action(
        "my-action-id",
        "Human title",
        "One-sentence description.",
        "python3 my-script.py --flag",
    ),
]
```

`make_action()` enforces `safe=True` and non-empty `command` at call time; a ValueError is raised if either invariant is violated. This prevents accidentally publishing write-capable actions.

Route-specific optional fields are passed as keyword arguments:

```python
# sync.py — include gateway context
make_action(..., requires_configured_gateway=False)

# scout.py — include target context
make_action(..., requires_configured_target=True)
```

## Frontend Implementation

### Type (`browse-ui/src/lib/api/types.ts`)

A single shared `OperatorAction` interface covers all 4 routes:

```typescript
export interface OperatorAction {
  id: string;
  title: string;
  description: string;
  command: string;
  safe: boolean;
  requires_configured_gateway?: boolean; // sync only
  requires_configured_target?: boolean;  // scout only
}
```

### Zod Schema (`browse-ui/src/lib/api/schemas.ts`)

```typescript
export const operatorActionSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string(),
  command: z.string(),
  safe: z.literal(true),                          // enforced — not just boolean
  requires_configured_gateway: z.boolean().optional(),
  requires_configured_target: z.boolean().optional(),
});
```

`safe: z.literal(true)` is intentional — parsing a response with `safe: false` is a contract violation and should throw at the boundary, not silently pass.

### Component (`browse-ui/src/components/data/operator-actions-panel.tsx`)

Settings-page rendering is handled by the shared `OperatorActionsPanel` component:

```tsx
<OperatorActionsPanel
  actions={someEndpoint.data.operator_actions}
  note="Copy-only safe commands. Browser does not execute these operations."
/>
```

Props:
- `actions: OperatorAction[]` — the actions array from the API response
- `label?: string` — heading; defaults to `"Operator checks (read-only)"`
- `note?: string` — secondary disclaimer; defaults to a generic copy-only note

If `actions` is empty, the component renders nothing.

## Adding a New Route

1. **Backend**: Use `make_action()` in your route handler. Pass route-specific context kwargs if applicable. Verify `safe=True` is never overridden.

2. **Frontend types**: Add `operator_actions: OperatorAction[]` to your new response interface.

3. **Frontend schemas**: Add `operator_actions: z.array(operatorActionSchema)` to your new response schema.

4. **Settings page**: Add `<OperatorActionsPanel actions={yourData.operator_actions} note="..." />` where applicable.

5. **Tests**:
   - In `test_browse_api.py`: verify the new route's `operator_actions` list passes the shared contract assertions (see T21 pattern).
   - In `schemas.test.ts`: add a parse test for the new response schema that includes `operator_actions`.

## Test Coverage

| Layer | Location | What it checks |
|---|---|---|
| Backend contract | `test_browse_api.py` T21 | All 4 routes emit required fields, `safe=True`, non-empty command |
| Backend route-specific | `test_browse_api.py` T22 | Sync emits `requires_configured_gateway`; scout emits `requires_configured_target`; tentacles/skills omit both |
| Frontend schema | `schemas.test.ts` | `operatorActionSchema` accepts valid actions, rejects `safe=false`, rejects missing fields; tentacle/skill response schemas use shared schema |
| Backend unit | `browse/core/operator_actions.py` | `make_action()` raises on `safe=False` or empty command |

## Invariants (non-negotiable)

- `safe` must always be `true`. This is a display-only contract; these commands may appear in a web UI and must never imply write or destructive operations.
- `command` must be non-empty. An empty command string is meaningless and indicates a construction bug.
- No new required fields may be added to `OperatorAction` without updating **all** 4 routes, the Python `make_action()` signature, the TypeScript interface, the Zod schema, and the test suite simultaneously.
- The `OperatorActionsPanel` component must not add a submit/run button or any mechanism that executes the command.
