const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const express = require("express");
const pty = require("node-pty");
const qrcode = require("qrcode-terminal");
const { Server } = require("socket.io");
const {
  Tunnel,
  bin: cloudflaredBin,
  install: installCloudflared,
} = require("cloudflared");

const DEFAULT_PORT = 2208;
const DEFAULT_HOST = "0.0.0.0";
const DEFAULT_COLS = 120;
const DEFAULT_ROWS = 40;
const DEFAULT_TERM = "xterm-256color";
const PUBLIC_DIR = path.join(__dirname, "public");
const XTERM_DIR = path.dirname(require.resolve("@xterm/xterm/package.json"));
const XTERM_ADDON_DIR = path.dirname(require.resolve("@xterm/addon-fit/package.json"));

function parseBoolean(value) {
  if (value === undefined || value === null) {
    return false;
  }

  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
}

function resolvePort(value) {
  const raw = value ?? process.env.REMOTE_TERMINAL_PORT ?? DEFAULT_PORT;
  const parsed = Number.parseInt(String(raw), 10);
  if (!Number.isInteger(parsed) || parsed < 0 || parsed > 65535) {
    throw new Error(`Invalid remote terminal port: ${raw}`);
  }
  return parsed;
}

function buildAccessUrl(baseUrl, token) {
  const url = new URL(baseUrl);
  url.searchParams.set("token", token);
  return url.toString();
}

function stripTokenFromUrl(urlValue) {
  if (!urlValue) {
    return null;
  }

  const url = new URL(urlValue);
  url.search = "";
  return url.toString();
}

function pickAccessHost(explicitHost) {
  if (explicitHost) {
    return explicitHost;
  }

  const interfaces = os.networkInterfaces();
  for (const entries of Object.values(interfaces)) {
    for (const entry of entries ?? []) {
      if (entry && entry.family === "IPv4" && !entry.internal) {
        return entry.address;
      }
    }
  }

  return "127.0.0.1";
}

function resolveShell(command, args) {
  if (command) {
    return {
      command,
      args: Array.isArray(args) ? args : [],
    };
  }

  const configuredShell = process.env.REMOTE_TERMINAL_SHELL;
  if (configuredShell) {
    return {
      command: configuredShell,
      args: (process.env.REMOTE_TERMINAL_SHELL_ARGS || "")
        .split(/\s+/)
        .filter(Boolean),
    };
  }

  if (process.platform === "win32") {
    return {
      command: "powershell.exe",
      args: ["-NoLogo"],
    };
  }

  return {
    command: process.env.SHELL || "/bin/bash",
    args: ["-i"],
  };
}

function createToken(explicitToken) {
  return explicitToken || process.env.REMOTE_TERMINAL_TOKEN || crypto.randomBytes(24).toString("hex");
}

function isValidToken(expectedToken, candidate) {
  if (typeof candidate !== "string") {
    return false;
  }

  const expected = Buffer.from(expectedToken);
  const actual = Buffer.from(candidate);
  return expected.length === actual.length && crypto.timingSafeEqual(expected, actual);
}

function renderQrCode(url, { label, logger, qrWriter }) {
  logger(`${label}: ${url}`);
  if (qrWriter) {
    qrWriter(label, url);
    return;
  }

  qrcode.generate(url, { small: true }, (rendered) => {
    logger(rendered.trimEnd());
  });
}

function createTunnel(localTarget, token, { logger, qrWriter, setPublicUrl, setTunnelError, tunnelFactory }) {
  const tunnel = tunnelFactory(localTarget);
  let published = false;

  tunnel.once("url", (url) => {
    published = true;
    setTunnelError(null);
    const publicUrl = buildAccessUrl(url, token);
    setPublicUrl(publicUrl);
    renderQrCode(publicUrl, {
      label: "Quick tunnel QR",
      logger,
      qrWriter,
    });
  });

  tunnel.once("error", (error) => {
    setPublicUrl(null);
    setTunnelError(error.message);
    logger(`Cloudflare Quick Tunnel failed: ${error.message}`);
  });

  tunnel.once("exit", (code, signal) => {
    setPublicUrl(null);
    if (!published) {
      setTunnelError(`cloudflared exited before publishing a URL (code=${code ?? "null"}, signal=${signal ?? "none"})`);
      logger(
        `Cloudflare Quick Tunnel exited before publishing a URL (code=${code ?? "null"}, signal=${signal ?? "none"}).`,
      );
    } else {
      setTunnelError(`cloudflared exited (code=${code ?? "null"}, signal=${signal ?? "none"})`);
    }
  });

  return tunnel;
}

async function ensureCloudflaredBinary(logger) {
  if (fs.existsSync(cloudflaredBin)) {
    return;
  }

  logger(`Installing cloudflared binary at ${cloudflaredBin} ...`);
  await installCloudflared(cloudflaredBin);
}

async function listen(server, port, host) {
  await new Promise((resolve, reject) => {
    const onError = (error) => {
      server.off("listening", onListening);
      reject(error);
    };
    const onListening = () => {
      server.off("error", onError);
      resolve();
    };

    server.once("error", onError);
    server.once("listening", onListening);
    server.listen(port, host);
  });
}

async function startRemoteTerminal(options = {}) {
  const logger = options.logger || console.log;
  const qrWriter = options.qrWriter;
  const host = options.host || process.env.REMOTE_TERMINAL_HOST || DEFAULT_HOST;
  const accessHost = pickAccessHost(options.accessHost || process.env.REMOTE_TERMINAL_ACCESS_HOST);
  const port = resolvePort(options.port);
  const token = createToken(options.token);
  const shell = resolveShell(options.shellCommand, options.shellArgs);
  const cwd = options.cwd || process.cwd();
  const env = {
    ...process.env,
    TERM: DEFAULT_TERM,
    ...(options.env || {}),
  };
  const tunnelEnabled = options.disableTunnel
    ? false
    : !parseBoolean(process.env.REMOTE_TERMINAL_DISABLE_TUNNEL);

  const app = express();
  app.disable("x-powered-by");

  let publicUrl = null;
  let tunnel = null;
  let stopPromise = null;
  let closedResolve;
  let shellRunning = true;
  let shellExit = null;
  let tunnelError = null;
  const closed = new Promise((resolve) => {
    closedResolve = resolve;
  });
  const lastResize = {
    cols: DEFAULT_COLS,
    rows: DEFAULT_ROWS,
  };

  const ptyFactory = options.ptyFactory || pty.spawn;
  const ptyProcess = ptyFactory(shell.command, shell.args, {
    name: DEFAULT_TERM,
    cols: DEFAULT_COLS,
    rows: DEFAULT_ROWS,
    cwd,
    env,
  });

  const server = http.createServer(app);
  const io = new Server(server, {
    serveClient: true,
  });

  function setPublicUrl(url) {
    publicUrl = url;
  }

  function setTunnelError(message) {
    tunnelError = message;
  }

  async function stop() {
    if (stopPromise) {
      return stopPromise;
    }

    stopPromise = (async () => {
      if (tunnel) {
        tunnel.stop();
      }

      if (shellRunning) {
        shellRunning = false;
        ptyProcess.kill();
      }

      await Promise.allSettled([
        new Promise((resolve) => io.close(() => resolve())),
        new Promise((resolve) => server.close(() => resolve())),
      ]);

      closedResolve();
    })();

    return stopPromise;
  }

  app.get("/health", (_req, res) => {
    res.json({
      ok: true,
      port: server.address().port,
      localOrigin: stripTokenFromUrl(buildAccessUrl(`http://${accessHost}:${server.address().port}/`, token)),
      publicOrigin: stripTokenFromUrl(publicUrl),
      tunnelError,
      shell: shell.command,
      shellArgs: shell.args,
      shellExit,
      lastResize,
      tunnelEnabled,
    });
  });

  app.get("/", (req, res) => {
    if (!isValidToken(token, req.query.token)) {
      res.status(401).type("text/plain").send("missing or invalid token");
      return;
    }

    res.sendFile(path.join(PUBLIC_DIR, "index.html"));
  });

  app.get("/client.js", (_req, res) => {
    res.type("application/javascript").sendFile(path.join(PUBLIC_DIR, "client.js"));
  });

  app.get("/vendor/xterm.css", (_req, res) => {
    res.type("text/css").sendFile(path.join(XTERM_DIR, "css", "xterm.css"));
  });

  app.get("/vendor/xterm.js", (_req, res) => {
    res.type("application/javascript").sendFile(path.join(XTERM_DIR, "lib", "xterm.js"));
  });

  app.get("/vendor/xterm-addon-fit.js", (_req, res) => {
    res.type("application/javascript").sendFile(path.join(XTERM_ADDON_DIR, "lib", "addon-fit.js"));
  });

  app.get("/favicon.ico", (_req, res) => {
    res.status(204).end();
  });

  io.use((socket, next) => {
    const providedToken = socket.handshake.auth.token || socket.handshake.query.token;
    if (!isValidToken(token, Array.isArray(providedToken) ? providedToken[0] : providedToken)) {
      next(new Error("unauthorized"));
      return;
    }

    next();
  });

  io.on("connection", (socket) => {
    socket.emit("output", "");
    socket.on("input", (chunk) => {
      if (typeof chunk === "string" && shellRunning) {
        ptyProcess.write(chunk);
      }
    });

    socket.on("resize", (payload) => {
      const cols = Number.parseInt(String(payload?.cols), 10);
      const rows = Number.parseInt(String(payload?.rows), 10);
      if (Number.isInteger(cols) && Number.isInteger(rows) && cols > 0 && rows > 0) {
        lastResize.cols = cols;
        lastResize.rows = rows;
        ptyProcess.resize(cols, rows);
      }
    });
  });

  ptyProcess.onData((data) => {
    io.emit("output", data);
  });

  ptyProcess.onExit(({ exitCode, signal }) => {
    shellRunning = false;
    shellExit = {
      exitCode,
      signal,
    };
    io.emit("session-exit", shellExit);
    logger(`Shell exited (code=${exitCode ?? "null"}, signal=${signal ?? "none"}).`);
    void stop();
  });

  try {
    await listen(server, port, host);
  } catch (error) {
    shellRunning = false;
    ptyProcess.kill();
    io.close();
    throw error;
  }

  const activePort = server.address().port;
  const localUrl = buildAccessUrl(`http://${accessHost}:${activePort}/`, token);
  logger(`Remote terminal listening on http://${host}:${activePort}`);
  logger(`Shell: ${shell.command}${shell.args.length ? ` ${shell.args.join(" ")}` : ""}`);
  renderQrCode(localUrl, {
    label: "Local access QR",
    logger,
    qrWriter,
  });

  if (tunnelEnabled) {
    try {
      if (!options.tunnelFactory) {
        await ensureCloudflaredBinary(logger);
      }
      logger(`Starting Cloudflare Quick Tunnel for http://127.0.0.1:${activePort} ...`);
      tunnel = createTunnel(`http://127.0.0.1:${activePort}`, token, {
        logger,
        qrWriter,
        setPublicUrl,
        setTunnelError,
        tunnelFactory: options.tunnelFactory || ((target) => Tunnel.quick(target)),
      });
    } catch (error) {
      tunnelError = error instanceof Error ? error.message : String(error);
      logger(`Cloudflare Quick Tunnel unavailable: ${tunnelError}`);
    }
  }

  return {
    port: activePort,
    host,
    accessHost,
    token,
    localUrl,
    get publicUrl() {
      return publicUrl;
    },
    lastResize,
    closed,
    server,
    io,
    ptyProcess,
    shell,
    stop,
  };
}

async function main() {
  const remoteTerminal = await startRemoteTerminal();
  const shutdown = async () => {
    await remoteTerminal.stop();
    process.exit(0);
  };

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

module.exports = {
  DEFAULT_PORT,
  buildAccessUrl,
  isValidToken,
  pickAccessHost,
  resolvePort,
  stripTokenFromUrl,
  startRemoteTerminal,
};

if (require.main === module) {
  main().catch((error) => {
    console.error(error.stack || error.message);
    process.exit(1);
  });
}
