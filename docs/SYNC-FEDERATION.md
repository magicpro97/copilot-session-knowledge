# Sync Federation — Deployment Guide

Production deployment of `sync-gateway.py` with per-repo namespace scoping
and visibility-based entry filtering.

## Architecture

```
┌──────────┐   push/pull    ┌─────────────┐   push/pull    ┌──────────┐
│ Replica A │ ────────────→ │   Gateway    │ ←──────────── │ Replica B │
│ ns=repo-x │  ?namespace=  │  (central)   │  ?namespace=  │ ns=repo-x │
└──────────┘   repo-x      └─────────────┘   repo-x      └──────────┘
                                  │
                            SQLite (WAL)
```

Each replica pushes/pulls with a `namespace` parameter that scopes its
transactions to a specific repository.  The gateway stores all namespaces
in one database but isolates reads.

## Namespace Scoping

- **Push**: include `"namespace": "<repo-slug>"` in the POST body.
  Default is `"default"` if omitted.
- **Pull**: include `?namespace=<repo-slug>` in the query string.
  Default is `"default"` if omitted.
- Transactions from namespace A are never returned in pulls for namespace B.
- The `sync-config.json` file can set a default namespace:

```json
{
  "connection_string": "http://gateway.example.com:8787",
  "namespace": "myorg/myrepo"
}
```

## Visibility Flags

Entries in `knowledge_entries` now have a `visibility` column:

| Value     | Behavior                                          |
|-----------|---------------------------------------------------|
| `public`  | Included in outbound sync operations (default)    |
| `private` | Stored locally, **never** sent to other replicas  |

### Marking entries private

```bash
# Via sk learn
sk learn --private "API key rotation procedure for prod"

# Via direct SQL (admin)
UPDATE knowledge_entries SET visibility = 'private' WHERE id = 42;
```

### How it works

- On **push**, all entries (public + private) are sent to the gateway.
  The gateway stores everything.
- On **pull**, the gateway filters out ops whose `row_payload` contains
  `"visibility": "private"` before returning them to the requesting replica.
- This means private entries exist only on the originating replica's
  local database.

## Deployment

### Prerequisites

- Python 3.10+ (stdlib only, no pip dependencies)
- A host with persistent storage for the SQLite file

### Standalone

```bash
# Start with token auth (recommended)
export SYNC_GATEWAY_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 sync-gateway.py --host 0.0.0.0 --port 8787 --token "$SYNC_GATEWAY_TOKEN"
```

### Behind a reverse proxy (nginx)

```nginx
server {
    listen 443 ssl;
    server_name sync.example.com;

    ssl_certificate     /etc/ssl/certs/sync.pem;
    ssl_certificate_key /etc/ssl/private/sync.key;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        client_max_body_size 10m;
    }
}
```

### Docker

```dockerfile
FROM python:3.12-slim
COPY sync-gateway.py /app/
WORKDIR /app
ENV SYNC_GATEWAY_TOKEN=""
EXPOSE 8787
CMD ["python3", "sync-gateway.py", "--host", "0.0.0.0", "--port", "8787"]
```

```bash
docker build -t sync-gateway .
docker run -d \
  -p 8787:8787 \
  -e SYNC_GATEWAY_TOKEN="your-secret-token" \
  -v sync-data:/root/.copilot/session-state \
  sync-gateway
```

### systemd

```ini
[Unit]
Description=SK Sync Gateway
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/sk/sync-gateway.py --host 0.0.0.0 --port 8787
Environment=SYNC_GATEWAY_TOKEN=your-secret-token
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Client Configuration

On each replica, set the connection string and namespace:

```bash
# Create or update sync-config.json
cat > ~/.copilot/tools/sync-config.json << 'EOF'
{
  "connection_string": "http://sync.example.com:8787",
  "namespace": "myorg/myrepo"
}
EOF
```

## Monitoring

```bash
# Check sync status including namespace and visibility stats
sk sync status

# JSON output for automation
sk sync status --json

# Health check (exit code 0 = healthy, 2 = degraded)
sk sync status --health-check
```

## Environment Variables

| Variable              | Default      | Description                              |
|-----------------------|--------------|------------------------------------------|
| `SYNC_GATEWAY_TOKEN`  | (empty)      | Bearer token for push/pull auth          |
| `SYNC_MAX_BODY_BYTES` | `10485760`   | Max POST body size (10 MB)               |

## Backward Compatibility

- `--auto` file-based merge (`sync-knowledge.py --auto`) works without
  a gateway, unchanged.
- Replicas that do not send a `namespace` parameter default to
  `"default"`, which matches pre-federation behavior.
- The `visibility` column defaults to `'public'`, so existing entries
  are unaffected.
