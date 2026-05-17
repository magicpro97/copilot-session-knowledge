# Remote Terminal MVP

This package adds a token-gated browser terminal backed by:

- a detached PTY daemon subprocess for crash isolation
- `node-pty` for the PTY session
- `Socket.IO` for bidirectional streaming
- `xterm.js` for the browser terminal
- `systray2` for an optional native system tray menu without Electron
- `cloudflared` for zero-config Cloudflare Quick Tunnel URLs
- `qrcode-terminal` for scannable QR codes in the operator console

## Usage

```powershell
cd remote-terminal
npm install
npm start
```

## Quality baseline

```powershell
cd remote-terminal
npm ci
npm test
npm run lint
npm run lint:clean
npm run audit:high
```

`npm run lint` uses ESLint flat config with warning-only complexity, size, parameter, and unused-variable rules so the current large-file baseline is visible without blocking normal development. Clean files (`pty-daemon.js` and `test/client.test.js`) promote the same rules to errors and are checked by `npm run lint:clean` with `--max-warnings=0`. The CI job runs lint after tests, blocks on the clean-zone lint gate, and runs `npm run audit:high` as a blocking high-severity dependency audit because the current dependency baseline is clean.

By default the server:

1. listens on `0.0.0.0:2208`
2. generates a one-time QR token that expires after 30 minutes
3. prints a LAN QR code immediately
4. starts a Cloudflare Quick Tunnel state machine with an `ora` progress spinner
5. retries tunnel setup after disconnects / tunnel errors with exponential backoff
6. keeps the PTY session inside a detached daemon so active shells survive server restarts
7. prints a public QR code once the tunnel URL is verified
8. lets the browser switch between six terminal themes that persist in local storage
9. exposes a touch-friendly on-screen keyboard for Ctrl / Alt / Shift, arrows, Tab, and F1-F12
10. enables a desktop system tray menu when the host has a local GUI session

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
| `REMOTE_TERMINAL_ENABLE_TRAY` | Force-enable the native tray integration even outside the default desktop detection | unset |
| `REMOTE_TERMINAL_DISABLE_TRAY` | Force-disable the native tray integration | unset / `false` |

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
- The browser UI ships with six themes: Default, Light, Dracula, Monokai, Solarized Dark, and Solarized Light. The selected theme persists in the current browser via `localStorage`.
- Phones and tablets can toggle a touch keyboard that adds modifier keys, arrows, Tab, Enter, Backspace, and F1-F12 without stealing focus from the PTY session.
- Generated QR tokens are one-time session keys: after 30 minutes they stop authorizing new page loads and new Socket.IO connections, but existing terminal sessions are left alone until they disconnect.
- Failed HTTP and WebSocket auth attempts are rate limited to 5 tries per 60 seconds per client IP to make token guessing noisy and self-limiting.
- When traffic is relayed through a local `cloudflared` process, the rate limiter prefers `CF-Connecting-IP` / `X-Forwarded-For` over the loopback relay address so different remote clients do not share one auth bucket.
- `REMOTE_TERMINAL_TOKEN` is the supported trusted-device flow for operators who want a stable QR code or a bookmarkable fixed URL.
- The PTY daemon is a detached subprocess: restarting the HTTP / Socket.IO server reattaches to the existing shell session instead of killing it.
- If the PTY daemon crashes, the server respawns it after 1 second and reconnects over the same local IPC endpoint.
- When the host has a desktop session, the operator gets a native `systray2` menu with live tunnel status, a copy-URL action, and a "Regenerate QR code" shortcut. macOS uses a template tray icon so the menu bar adapts to light/dark appearance automatically.
- `Ctrl+C`, `Ctrl+D`, tab completion, and resize handling are delegated to the real PTY-backed shell, so shell behavior stays native instead of being emulated in JavaScript.
- For LAN-only smoke tests, start with `REMOTE_TERMINAL_DISABLE_TUNNEL=1 npm start`.
- `/health` exposes the tunnel state machine (`tunnelState`, `tunnelRetryDelayMs`, `tunnelError`), daemon state (`backendMode`, `daemonConnected`, `daemonEndpoint`, `daemonPid`), tray state (`trayEnabled`, `trayReady`, `trayStatus`, `trayError`), and auth metadata (`persistentToken`, `tokenExpiresAt`) without leaking the access token.
- If your local Windows environment has a custom `cmd.exe` / PATH setup that prevents npm lifecycle scripts from seeing `node`, run `npm --script-shell pwsh <command>` for local verification. That is an environment workaround, not a package requirement.
