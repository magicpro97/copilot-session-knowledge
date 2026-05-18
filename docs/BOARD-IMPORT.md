# Board Import Automation & Issue JSONL Generator

> **Issue #425** — Operator reference for batch-creating GitHub issues from WBS definitions and
> adding them to a Project v2 board with priority fields set.

## Overview

The board-import workflow converts a Work Breakdown Structure (WBS) in JSONL format into GitHub
issues and wires them into a GitHub Project v2 board. It uses only `gh` CLI, `curl`, and
`python` (stdlib) — no additional dependencies required.

**Tested pattern (from production WBS burns):** REST API for issue creation + PowerShell/
`Invoke-RestMethod` GraphQL for Project v2 field mutations. The `gh project` subcommand can
hit unexpected EOF on large batches; REST + explicit GraphQL are more reliable.

---

## JSONL Schema

Each line in the input `.jsonl` file is a JSON object with these fields:

```jsonc
{
  // Required
  "title": "WBS-042: Implement adaptive recall scoring",

  // Optional — GitHub Flavored Markdown
  "body": "## Goal\n\nAdd a relevance score to briefing entries.\n\n## Acceptance Criteria\n\n- [ ] Score is computed without network access\n- [ ] Score persists in knowledge.db",

  // Optional — existing label names (created if missing when using --ensure-labels)
  "labels": ["wbs", "p1", "enhancement"],

  // Optional — milestone title (must already exist unless --ensure-milestone)
  "milestone": "Wave 2",

  // Optional — GitHub usernames
  "assignees": ["octocat"],

  // Optional — project priority value for GitHub Project v2 single-select field
  // Accepted: "P0", "P1", "P2", "P3" (case-insensitive)
  "priority": "P1",

  // Optional — arbitrary key/value for dry-run introspection; never sent to GitHub
  "context": {
    "wave": "2b",
    "surface": "retrieval",
    "wbs_id": "WBS-042"
  }
}
```

### Minimal valid entry

```json
{"title": "WBS-042: Implement adaptive recall scoring"}
```

### Full example entry

```json
{
  "title": "WBS-042: Implement adaptive recall scoring",
  "body": "## Goal\n\nAdd relevance scoring to briefing entries.\n\n## Acceptance\n\n- [ ] Offline-only\n- [ ] Persisted in DB",
  "labels": ["wbs", "p1"],
  "milestone": "Wave 2",
  "assignees": [],
  "priority": "P1",
  "context": {"wave": "2b", "wbs_id": "WBS-042"}
}
```

---

## JSONL Validator / Generator (`wbs-issue-gen.py`)

The generator is checked in at [`wbs-issue-gen.py`](../wbs-issue-gen.py) (repo root) — stdlib
only, no installation needed. Run it directly:

```bash
# Validate an existing JSONL file (offline, no network)
python wbs-issue-gen.py --validate wbs-issues.jsonl

# Generate N example entries
python wbs-issue-gen.py --generate 5 --output wbs-issues.jsonl

# Dry-run: print planned REST payloads (context and priority are excluded)
python wbs-issue-gen.py --dry-run wbs-issues.jsonl

# Print schema reference
python wbs-issue-gen.py --schema
```

---

## Import Workflow

### Prerequisites

```bash
# Verify gh is authenticated
gh auth status

# Set env vars
export REPO="owner/repo"               # e.g. magicpro97/copilot-session-knowledge
export PROJECT_NUMBER=1                # GitHub Project v2 number (from project URL)
export GITHUB_TOKEN=$(gh auth token)
```

### Step 1 — Validate your JSONL (offline, no network)

```bash
python wbs-issue-gen.py --validate wbs-issues.jsonl
```

### Step 2 — Dry-run (print planned REST calls, no network)

```bash
python wbs-issue-gen.py --dry-run wbs-issues.jsonl
```

The `--dry-run` flag prints each REST payload that would be sent, excluding `context`
(operator-only metadata) and `priority` (set via GraphQL in Step 4).

### Step 3 — Create issues via GitHub REST API

```bash
python - <<'PYEOF'
import json, os, sys, urllib.request, urllib.error

REPO  = os.environ["REPO"]
TOKEN = os.environ["GITHUB_TOKEN"]
URL   = f"https://api.github.com/repos/{REPO}/issues"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "Content-Type": "application/json",
}

created = []
with open("wbs-issues.jsonl", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entry = json.loads(line)
        payload = {k: entry[k] for k in ("title","body","labels","milestone","assignees") if k in entry}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(URL, data=data, headers=HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                issue = json.load(resp)
                print(f"  Created #{issue['number']}: {issue['title']}")
                created.append({"number": issue["number"], "node_id": issue["node_id"],
                                "priority": entry.get("priority")})
        except urllib.error.HTTPError as exc:
            print(f"  ERROR {exc.code}: {exc.read().decode()}", file=sys.stderr)

# Save for Step 4
with open("created-issues.json", "w", encoding="utf-8") as out:
    json.dump(created, out, indent=2)
print(f"\nDone. {len(created)} issues created. See created-issues.json.")
PYEOF
```

### Step 4 — Add issues to Project v2 and set Priority field (PowerShell)

```powershell
# Run on Windows with PowerShell, or via pwsh on Linux/macOS
$repo          = $env:REPO
$projectNumber = [int]$env:PROJECT_NUMBER
$token         = $env:GITHUB_TOKEN
$headers       = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" }

# Get project node ID
$q = @{ query = "query { repository(owner: `"$($repo.Split('/')[0])`", name: `"$($repo.Split('/')[1])`") {
  projectV2(number: $projectNumber) { id fields(first:20) { nodes { ... on ProjectV2SingleSelectField { id name options { id name } } } } } } }" }
$r = Invoke-RestMethod "https://api.github.com/graphql" -Method POST -Body ($q|ConvertTo-Json) -Headers $headers
$proj   = $r.data.repository.projectV2
$projId = $proj.id
$prioField = $proj.fields.nodes | Where-Object { $_.name -eq "Priority" }
$prioFieldId = $prioField.id

$issues = Get-Content "created-issues.json" | ConvertFrom-Json

foreach ($iss in $issues) {
    # 1. Add issue to project
    $addQ = @{ query = "mutation { addProjectV2ItemById(input: { projectId: `"$projId`" contentId: `"$($iss.node_id)`" }) { item { id } } }" }
    $addR = Invoke-RestMethod "https://api.github.com/graphql" -Method POST -Body ($addQ|ConvertTo-Json) -Headers $headers
    $itemId = $addR.data.addProjectV2ItemById.item.id

    # 2. Set Priority field if declared
    if ($iss.priority -and $prioFieldId) {
        $optId = ($prioField.options | Where-Object { $_.name -ieq $iss.priority }).id
        if ($optId) {
            $setQ = @{ query = "mutation { updateProjectV2ItemFieldValue(input: {
              projectId: `"$projId`" itemId: `"$itemId`"
              fieldId: `"$prioFieldId`" value: { singleSelectOptionId: `"$optId`" }
            }) { projectV2Item { id } } }" }
            Invoke-RestMethod "https://api.github.com/graphql" -Method POST -Body ($setQ|ConvertTo-Json) -Headers $headers | Out-Null
        }
    }
    Write-Host "  Board: #$($iss.number) → itemId=$itemId priority=$($iss.priority)"
}
Write-Host "Done."
```

---

## Idempotency & Re-runs

- **Duplicate detection:** Before re-running Step 3, check for existing issues with the same title
  to avoid duplicates:
  ```bash
  gh issue list --repo "$REPO" --label wbs --limit 200 --json number,title
  ```
- **Priority reset:** Re-running Step 4 with the same `created-issues.json` is safe — it
  overwrites the Priority field value.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `unexpected EOF` from `gh project` | Use the REST + GraphQL approach above instead |
| `Resource not accessible by integration` | Add `projects: write` to the GITHUB_TOKEN scope |
| Priority field not found | Verify the field name is exactly `"Priority"` in the project settings |
| Label not found | Create labels first with `gh label create wbs --color 0075ca` |
| Rate-limited | Add `time.sleep(0.5)` between requests in Step 3 |

---

## Related Docs

- [docs/OPERATOR-PLAYBOOK.md](OPERATOR-PLAYBOOK.md) — full operator reference
- [docs/USAGE.md](USAGE.md) — `sk tentacle` orchestration commands
- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — script conventions (stdlib, parameterized SQL, no pickle)
