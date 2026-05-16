# Remote Terminal MVP

This package adds a token-gated browser terminal backed by:

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
2. generates a random token
3. prints a LAN QR code immediately
4. starts a Cloudflare Quick Tunnel and prints a public QR code once the tunnel URL is ready

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `REMOTE_TERMINAL_PORT` | HTTP / Socket.IO port | `2208` |
| `REMOTE_TERMINAL_HOST` | Bind host | `0.0.0.0` |
| `REMOTE_TERMINAL_TOKEN` | Fixed access token instead of a random one | random 48-char hex |
| `REMOTE_TERMINAL_ACCESS_HOST` | Override the QR code host/IP | first non-loopback IPv4 |
| `REMOTE_TERMINAL_SHELL` | Override the spawned shell executable | `powershell.exe` on Windows, `$SHELL` or `/bin/bash` elsewhere |
| `REMOTE_TERMINAL_DISABLE_TUNNEL` | Skip Cloudflare Quick Tunnel startup | unset / `false` |

## Notes

- Open the printed URL directly if you already have the token; the HTML page and the WebSocket both require the same token.
- `Ctrl+C`, `Ctrl+D`, tab completion, and resize handling are delegated to the real PTY-backed shell, so shell behavior stays native instead of being emulated in JavaScript.
- For LAN-only smoke tests, start with `REMOTE_TERMINAL_DISABLE_TUNNEL=1 npm start`.
- If your local Windows environment has a custom `cmd.exe` / PATH setup that prevents npm lifecycle scripts from seeing `node`, run `npm --script-shell pwsh <command>` for local verification. That is an environment workaround, not a package requirement.
