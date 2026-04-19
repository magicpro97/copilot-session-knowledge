# `.instructions.md` Template

> **Copy this file to** `.github/instructions/session-knowledge.instructions.md`
> in the target project. Replace `<PLACEHOLDER>` values with project-specific terms.
> The `applyTo: "**/*"` frontmatter causes Copilot CLI to auto-inject this file
> into every AI context — enforcement happens automatically.

---

```markdown
---
applyTo: "**/*"
---

# Session Knowledge — <PROJECT_NAME> (AUTO-LOADED)

> Auto-injected into every context. Knowledge tools are NOT optional.

## Before Starting ANY Task

```bash
python3 ~/.copilot/tools/briefing.py --auto --compact
```

Read the output — it contains past mistakes to avoid and patterns to follow for
<PROJECT_NAME>. Pay attention to entries tagged `<KEY_TAG_1>` or `<KEY_TAG_2>`.

## After Completing Work

Record what you learned (choose appropriate type):

```bash
# After fixing a bug:
python3 ~/.copilot/tools/learn.py --mistake "Title" "Root cause and fix" \
  --tags "<MODULE>,<TECH>" --wing <WING> --room <ROOM>

# After implementing a feature:
python3 ~/.copilot/tools/learn.py --feature "Title" "What was built" \
  --tags "<MODULE>,<TECH>" --wing <WING> --room <ROOM>

# After discovering a useful pattern:
python3 ~/.copilot/tools/learn.py --pattern "Title" "What works well" \
  --tags "<MODULE>,<TECH>"
```

## Rules

- ❌ NEVER skip briefing before starting complex work
- ❌ NEVER skip learn after fixing a non-trivial bug
- ✅ Use domain tags: `<KEY_TAG_1>`, `<KEY_TAG_2>`, `<KEY_TAG_3>`
- ✅ Keep entries concise (1-3 sentences max)
- ✅ Include wing/room for knowledge palace organization
```

---

## How to customize this template

| Placeholder | What to put there |
|-------------|-------------------|
| `<PROJECT_NAME>` | Actual project name (e.g. `NextShop`, `TravelApp`) |
| `<KEY_TAG_1/2/3>` | Domain tags derived from folder structure (e.g. `auth`, `api`, `ui`) |
| `<MODULE>` | Module/layer name from project (e.g. `backend`, `frontend`) |
| `<TECH>` | Technology stack (e.g. `kotlin`, `react`, `django`) |
| `<WING>` | Top-level architecture wing (e.g. `frontend`, `backend`, `infra`) |
| `<ROOM>` | Sub-module within the wing (e.g. `auth`, `products`, `payments`) |

Keep the file short (< 60 lines). Enforcement comes from auto-injection, not length.
