# Sync Gateway Template (Neon + Railway default)

This scaffold is a **provider-backed HTTP gateway template** for the local-first sync runtime.

## Contract preserved

- `POST /sync/push`
- `GET /sync/pull`
- `GET /healthz`

The core CLI/runtime still talks to an HTTP(S) gateway URL. This template does **not** make the CLI connect directly to Postgres/libSQL.

## Default rollout recommendation

- **Backing database:** Neon (Postgres)
- **Hosting:** Railway

These are recommended defaults, not hard lock-in.

## Environment

- `SYNC_GATEWAY_DATABASE_URL` (required)
- `PORT` (optional, Railway sets this)
- `HOST` (optional, default `0.0.0.0`)

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit SYNC_GATEWAY_DATABASE_URL in .env
set -a; source .env; set +a
python app.py
```

## Railway deploy notes

- `Procfile` and `railway.json` are included.
- Set `SYNC_GATEWAY_DATABASE_URL` in Railway variables.
- Keep SSL enabled in your Neon connection string (`sslmode=require`).
- The template enables `sleepApplication` for free-plan compatibility.
- The default deploy region is `asia-southeast1-eqsg3a`; change it in
  `railway.json` if you need a different Railway region.
