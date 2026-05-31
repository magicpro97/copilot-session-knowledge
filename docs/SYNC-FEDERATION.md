# Sync Federation (Issue #852)

Sync federation allows multiple repositories or teams to share a single sync gateway while keeping knowledge entries namespaced and visibility-controlled.

## Concepts

### Namespace
A namespace is a slug that identifies the origin of knowledge entries, typically derived from the git remote URL (e.g., `owner/repo`). It is auto-detected from `git remote get-url origin` if not configured explicitly.

### Visibility
Visibility controls which replicas receive a knowledge entry during pull:

| Value | Behaviour |
|-------|-----------|
| `private` | Only applied on replicas in the **same namespace** |
| `team` | Applied on **all** replicas connected to the gateway |
| `public` | Applied on **all** replicas connected to the gateway |

Default visibility is `private`.

## Configuration

```bash
# Set a fixed namespace slug (default: auto-detect from git remote)
python sync-config.py --set-namespace owner/my-repo

# Set the default visibility for new entries
python sync-config.py --set-visibility private    # default
python sync-config.py --set-visibility team
python sync-config.py --set-visibility public

# Show current federation config
python sync-config.py --status
```

## Recording knowledge with visibility

```bash
# Use --visibility when adding a learning entry
python learn.py --pattern "Shared pattern" "Details" --visibility team

# Default (private) — only synced to same-namespace replicas
python learn.py --mistake "Local bug" "Details"
```

## Push behaviour

When `sync-daemon.py` pushes transactions, the current namespace is included in the push payload (`namespace` field). The gateway uses this to route or tag entries.

Override the namespace at runtime:

```bash
python sync-daemon.py --once --namespace owner/other-repo
```

## Pull behaviour

During pull, each `knowledge_entries` op is filtered before being applied locally:

- `public` / `team` entries → always applied
- `private` entries → applied **only if** the entry's namespace matches the local namespace

This filtering is applied per-op inside each transaction, so a single transaction may contain a mix of public and private entries.

## Database schema

Migration v46 adds two columns to `knowledge_entries`:

```sql
ALTER TABLE knowledge_entries ADD COLUMN namespace TEXT DEFAULT 'local';
ALTER TABLE knowledge_entries ADD COLUMN visibility TEXT DEFAULT 'private';
CREATE INDEX IF NOT EXISTS idx_ke_namespace_visibility ON knowledge_entries (namespace, visibility);
```

Run `python migrate.py` to apply.

## Backward compatibility

- Existing entries without `namespace`/`visibility` default to `namespace='local'` and `visibility='private'`.
- Replicas without migration v46 continue to work; the new columns are added lazily on next `migrate.py` run.
- Sync behaviour is unchanged for users who do not configure a namespace or visibility.
