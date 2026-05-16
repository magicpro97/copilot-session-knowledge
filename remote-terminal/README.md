# Remote Terminal MVP

This package adds a token-gated browser terminal backed by:

- a detached PTY daemon subprocess for crash isolation
- `node-pty` for the PTY session
- `Socket.IO` for bidirectional streaming
- `xterm.js` for the browser terminal
- `cloudflared` for zero-config Cloudflare Quick Tunnel URLs
- `qrcode-terminal` for scannable QR codes in the operator console

## Usage

```powershell
cd remote-terminal
npm install
npm start
```

By default the server:

1. listens on `0.0.0.0:2208`
2. generates a one-time QR token that expires after 30 minutes
3. prints a LAN QR code immediately
4. starts a Cloudflare Quick Tunnel state machine with an `ora` progress spinner
5. retries tunnel setup after disconnects / tunnel errors with exponential backoff
6. keeps the PTY session inside a detached daemon so active shells survive server restarts
7. prints a public QR code once the tunnel URL is verified

## Tunnel states

```text
STOPPED → PREPARING → CONNECTING → TUNNELING → VERIFYING → READY
   ↑                                                      │
   └──────────── error / disconnect / verify fail ────────┘
```

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `REMOTE_TERMINAL_PORT` | HTTP / Socket.IO port | `2208` |
| `REMOTE_TERMINAL_HOST` | Bind host | `0.0.0.0` |
| `REMOTE_TERMINAL_TOKEN` | Fixed trusted-device token that stays valid across restarts and does not auto-expire | random 48-char hex one-time token |
| `REMOTE_TERMINAL_ACCESS_HOST` | Override the QR code host/IP | first non-loopback IPv4 |
| `REMOTE_TERMINAL_SHELL` | Override the spawned shell executable | `powershell.exe` on Windows, `$SHELL` or `/bin/bash` elsewhere |
| `REMOTE_TERMINAL_DISABLE_TUNNEL` | Skip Cloudflare Quick Tunnel startup | unset / `false` |

## PTY daemon IPC

The HTTP / Socket.IO server talks to `pty-daemon.js` over newline-delimited JSON on a local named pipe / Unix socket.

### Server -> daemon

| Message | Fields | Purpose |
| --- | --- | --- |
| `attach` | `sessionId`, `shell`, `cwd`, `env`, `cols`, `rows` | Reuse the existing PTY session or create it on first attach |
| `input` | `sessionId`, `data` | Forward keystrokes to the PTY |
| `resize` | `sessionId`, `cols`, `rows` | Keep the PTY geometry in sync with the browser |

### Daemon -> server

| Message | Fields | Purpose |
| --- | --- | --- |
| `attached` | `sessionId`, `reused`, `daemonPid`, `lastResize`, `shellExit` | Confirm the server is attached to the live PTY session |
| `output` | `sessionId`, `data` | Stream PTY output back to Socket.IO clients |
| `session_exit` | `sessionId`, `exitCode`, `signal` | Report that the shell session has exited |

## Notes

- Open the printed URL directly if you already have the token; the HTML page and the WebSocket both require the same token.
- Generated QR tokens are one-time session keys: after 30 minutes they stop authorizing new page loads and new Socket.IO connections, but existing terminal sessions are left alone until they disconnect.
- Failed HTTP and WebSocket auth attempts are rate limited to 5 tries per 60 seconds per client IP to make token guessing noisy and self-limiting.
- When traffic is relayed through a local `cloudflared` process, the rate limiter prefers `CF-Connecting-IP` / `X-Forwarded-For` over the loopback relay address so different remote clients do not share one auth bucket.
- `REMOTE_TERMINAL_TOKEN` is the supported trusted-device flow for operators who want a stable QR code or a bookmarkable fixed URL.
- The PTY daemon is a detached subprocess: restarting the HTTP / Socket.IO server reattaches to the existing shell session instead of killing it.
- If the PTY daemon crashes, the server respawns it after 1 second and reconnects over the same local IPC endpoint.
- `Ctrl+C`, `Ctrl+D`, tab completion, and resize handling are delegated to the real PTY-backed shell, so shell behavior stays native instead of being emulated in JavaScript.
- For LAN-only smoke tests, start with `REMOTE_TERMINAL_DISABLE_TUNNEL=1 npm start`.
- `/health` exposes the tunnel state machine (`tunnelState`, `tunnelRetryDelayMs`, `tunnelError`), daemon state (`backendMode`, `daemonConnected`, `daemonEndpoint`, `daemonPid`), and auth metadata (`persistentToken`, `tokenExpiresAt`) without leaking the access token.
- If your local Windows environment has a custom `cmd.exe` / PATH setup that prevents npm lifecycle scripts from seeing `node`, run `npm --script-shell pwsh <command>` for local verification. That is an environment workaround, not a package requirement.
