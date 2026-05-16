const crypto = require("node:crypto");
const { spawn } = require("node:child_process");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
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
const DEFAULT_TUNNEL_RETRY_BASE_MS = 1000;
const DEFAULT_TUNNEL_RETRY_MAX_MS = 30000;
const DEFAULT_TUNNEL_VERIFY_ATTEMPTS = 12;
const DEFAULT_TUNNEL_VERIFY_DELAY_MS = 5000;
const DEFAULT_AUTH_TOKEN_TTL_MS = 30 * 60 * 1000;
const DEFAULT_AUTH_RATE_LIMIT_WINDOW_MS = 60 * 1000;
const DEFAULT_AUTH_RATE_LIMIT_MAX_ATTEMPTS = 5;
const DEFAULT_DAEMON_RESTART_DELAY_MS = 1000;
const DEFAULT_DAEMON_SESSION_ID = "default";
const PUBLIC_DIR = path.join(__dirname, "public");
const PTY_DAEMON_SCRIPT = path.join(__dirname, "pty-daemon.js");
const XTERM_DIR = path.dirname(require.resolve("@xterm/xterm/package.json"));
const XTERM_ADDON_DIR = path.dirname(require.resolve("@xterm/addon-fit/package.json"));
const TUNNEL_STATES = Object.freeze({
  STOPPED: "STOPPED",
  PREPARING: "PREPARING",
  CONNECTING: "CONNECTING",
  TUNNELING: "TUNNELING",
  VERIFYING: "VERIFYING",
  READY: "READY",
});

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

function createAccessToken(explicitToken, options = {}) {
  const configuredToken = explicitToken || process.env.REMOTE_TERMINAL_TOKEN || null;
  const now = options.now || Date.now;
  return {
    value: configuredToken || crypto.randomBytes(24).toString("hex"),
    persistent: Boolean(configuredToken),
    expiresAt: configuredToken ? null : now() + (options.tokenTtlMs ?? DEFAULT_AUTH_TOKEN_TTL_MS),
  };
}

function isValidToken(expectedToken, candidate) {
  if (typeof candidate !== "string") {
    return false;
  }

  const expected = Buffer.from(expectedToken);
  const actual = Buffer.from(candidate);
  return expected.length === actual.length && crypto.timingSafeEqual(expected, actual);
}

function isTokenExpired(accessToken, now = Date.now) {
  return accessToken.expiresAt !== null && now() >= accessToken.expiresAt;
}

function validateAccessToken(accessToken, candidate, now = Date.now) {
  if (isTokenExpired(accessToken, now)) {
    return {
      ok: false,
      message: "access token expired",
      reason: "expired",
    };
  }

  return isValidToken(accessToken.value, candidate)
    ? { ok: true }
    : {
        ok: false,
        message: "missing or invalid token",
        reason: "invalid",
      };
}

function isLoopbackAddress(address) {
  return ["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(address);
}

function firstHeaderValue(value) {
  if (Array.isArray(value)) {
    return firstHeaderValue(value[0]);
  }

  return typeof value === "string" ? value.trim() : "";
}

function getForwardedClientId(headers = {}) {
  const cfConnectingIp = firstHeaderValue(headers["cf-connecting-ip"]);
  if (cfConnectingIp) {
    return cfConnectingIp;
  }

  const xForwardedFor = firstHeaderValue(headers["x-forwarded-for"]);
  if (!xForwardedFor) {
    return null;
  }

  const [firstClient] = xForwardedFor.split(",");
  const clientId = firstClient ? firstClient.trim() : "";
  return clientId || null;
}

function createAuthRateLimiter(options = {}) {
  const maxAttempts = options.maxAttempts ?? DEFAULT_AUTH_RATE_LIMIT_MAX_ATTEMPTS;
  const windowMs = options.windowMs ?? DEFAULT_AUTH_RATE_LIMIT_WINDOW_MS;
  const now = options.now || Date.now;
  const attemptsByClient = new Map();

  function getClientId(rawClientId) {
    return rawClientId || "unknown";
  }

  function pruneExpiredEntries(currentTime) {
    for (const [clientId, entry] of attemptsByClient.entries()) {
      if (currentTime >= entry.resetAt) {
        attemptsByClient.delete(clientId);
      }
    }
  }

  function getEntry(clientId) {
    const entry = attemptsByClient.get(clientId);
    return entry || null;
  }

  return {
    check(rawClientId) {
      const clientId = getClientId(rawClientId);
      const currentTime = now();
      pruneExpiredEntries(currentTime);
      const entry = getEntry(clientId);
      if (!entry || entry.count < maxAttempts) {
        return {
          limited: false,
          retryAfterMs: 0,
        };
      }

      return {
        limited: true,
        retryAfterMs: Math.max(0, entry.resetAt - currentTime),
      };
    },
    recordFailure(rawClientId) {
      const clientId = getClientId(rawClientId);
      const currentTime = now();
      pruneExpiredEntries(currentTime);
      const existing = getEntry(clientId);
      const entry = existing || {
        count: 0,
        resetAt: currentTime + windowMs,
      };
      entry.count += 1;
      attemptsByClient.set(clientId, entry);
      return entry.count;
    },
    reset(rawClientId) {
      attemptsByClient.delete(getClientId(rawClientId));
    },
  };
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

function createNoopSpinner() {
  return {
    isSpinning: false,
    text: "",
    start(text) {
      this.isSpinning = true;
      if (text) {
        this.text = text;
      }
      return this;
    },
    stop() {
      this.isSpinning = false;
      return this;
    },
    succeed(text) {
      this.isSpinning = false;
      if (text) {
        this.text = text;
      }
      return this;
    },
    warn(text) {
      this.isSpinning = false;
      if (text) {
        this.text = text;
      }
      return this;
    },
    fail(text) {
      this.isSpinning = false;
      if (text) {
        this.text = text;
      }
      return this;
    },
  };
}

async function createSpinner(spinnerFactory) {
  if (spinnerFactory) {
    return spinnerFactory();
  }

  const { default: ora } = await import("ora");
  return ora({
    discardStdin: false,
    text: `[${TUNNEL_STATES.STOPPED}] Tunnel idle`,
  });
}

function formatRetryDelay(delayMs) {
  if (delayMs >= 1000) {
    const seconds = delayMs / 1000;
    return Number.isInteger(seconds) ? `${seconds}s` : `${seconds.toFixed(1)}s`;
  }

  return `${delayMs}ms`;
}

function sleep(delayMs) {
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

function resolveDaemonEndpoint(cwd, port, explicitEndpoint) {
  if (explicitEndpoint) {
    return explicitEndpoint;
  }

  const scope = crypto.createHash("sha1").update(cwd).digest("hex").slice(0, 8);
  const endpointName = `remote-terminal-pty-daemon-${scope}-${port}`;
  if (process.platform === "win32") {
    return `\\\\.\\pipe\\${endpointName}`;
  }

  return path.join(os.tmpdir(), `${endpointName}.sock`);
}

function sendJsonLine(socket, message) {
  if (!socket.destroyed) {
    socket.write(`${JSON.stringify(message)}\n`);
  }
}

function bindJsonLineMessages(socket, onMessage) {
  let buffer = "";
  socket.on("data", (chunk) => {
    buffer += chunk.toString("utf8");
    while (buffer.includes("\n")) {
      const newlineIndex = buffer.indexOf("\n");
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (!line) {
        continue;
      }

      try {
        onMessage(JSON.parse(line));
      } catch (_error) {
        // Ignore malformed partial frames; recovery is driven by socket close.
      }
    }
  });
}

async function defaultVerifyTunnel(publicAccessUrl, options = {}) {
  const attempts = options.attempts ?? DEFAULT_TUNNEL_VERIFY_ATTEMPTS;
  const delayMs = options.delayMs ?? DEFAULT_TUNNEL_VERIFY_DELAY_MS;
  const healthUrl = new URL("health", stripTokenFromUrl(publicAccessUrl)).toString();
  let lastError = null;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    if (options.onProgress) {
      options.onProgress(attempt, attempts, healthUrl);
    }

    try {
      const response = await fetch(healthUrl, { method: "GET" });
      if (!response.ok) {
        throw new Error(`health check returned HTTP ${response.status}`);
      }
      return;
    } catch (error) {
      lastError = error;
      if (attempt < attempts) {
        await sleep(delayMs);
      }
    }
  }

  throw lastError || new Error("Tunnel verification failed");
}

function createTunnel(
  localTarget,
  token,
  { logger, qrWriter, setPublicUrl, setTunnelError, tunnelFactory, onConnected, onTunneling, onReadyRequested, onFailure },
) {
  const tunnel = tunnelFactory(localTarget);
  let connected = false;
  let publicAccessUrl = null;
  let failed = false;

  function fail(message) {
    if (failed) {
      return;
    }

    failed = true;
    setPublicUrl(null);
    setTunnelError(message);
    onFailure(message);
  }

  tunnel.once("url", (url) => {
    setTunnelError(null);
    publicAccessUrl = buildAccessUrl(url, token);
    setPublicUrl(publicAccessUrl);
    renderQrCode(publicAccessUrl, {
      label: "Quick tunnel QR",
      logger,
      qrWriter,
    });
    onTunneling(publicAccessUrl);
    if (connected) {
      onReadyRequested(publicAccessUrl);
    }
  });

  tunnel.once("connected", (connection) => {
    connected = true;
    onConnected(connection);
    if (publicAccessUrl) {
      onReadyRequested(publicAccessUrl);
    }
  });

  tunnel.once("disconnected", (connection) => {
    const location = connection && connection.location ? connection.location : "unknown";
    fail(`cloudflared disconnected (${location})`);
  });

  tunnel.once("error", (error) => {
    fail(error.message);
  });

  tunnel.once("exit", (code, signal) => {
    fail(`cloudflared exited (code=${code ?? "null"}, signal=${signal ?? "none"})`);
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

function createInlinePtyBackend(options = {}) {
  const backend = new EventEmitter();
  let shellRunning = true;
  let shellExit = null;
  const ptyFactory = options.ptyFactory || pty.spawn;
  const ptyProcess = ptyFactory(options.shell.command, options.shell.args, {
    cols: options.cols ?? DEFAULT_COLS,
    cwd: options.cwd,
    env: options.env,
    name: DEFAULT_TERM,
    rows: options.rows ?? DEFAULT_ROWS,
  });

  ptyProcess.onData((data) => {
    backend.emit("output", data);
  });

  ptyProcess.onExit(({ exitCode, signal }) => {
    shellRunning = false;
    shellExit = {
      exitCode,
      signal,
    };
    backend.emit("session-exit", shellExit);
  });

  return {
    backendMode: "inline",
    async close() {
      if (shellRunning) {
        shellRunning = false;
        ptyProcess.kill();
      }
    },
    daemonConnected: null,
    daemonEndpoint: null,
    daemonPid: null,
    off(eventName, listener) {
      backend.off(eventName, listener);
    },
    on(eventName, listener) {
      backend.on(eventName, listener);
    },
    get ptyProcess() {
      return ptyProcess;
    },
    resize(cols, rows) {
      if (shellRunning) {
        ptyProcess.resize(cols, rows);
      }
    },
    get shellExit() {
      return shellExit;
    },
    get shellRunning() {
      return shellRunning;
    },
    write(chunk) {
      if (shellRunning && typeof chunk === "string") {
        ptyProcess.write(chunk);
      }
    },
  };
}

async function createDaemonPtyBackend(options = {}) {
  const backend = new EventEmitter();
  backend.on("error", () => {});
  const daemonEndpoint = resolveDaemonEndpoint(options.cwd, options.port, options.daemonEndpoint);
  const daemonRestartDelayMs = options.daemonRestartDelayMs ?? DEFAULT_DAEMON_RESTART_DELAY_MS;
  const daemonScriptPath = options.daemonScriptPath || PTY_DAEMON_SCRIPT;
  const sessionId = options.sessionId || DEFAULT_DAEMON_SESSION_ID;
  const terminateDaemonOnClose = options.terminateDaemonOnClose ?? false;
  let daemonConnected = false;
  let daemonPid = null;
  let socket = null;
  let recoveryTimer = null;
  let recovering = false;
  let stopping = false;

  function clearRecoveryTimer() {
    if (recoveryTimer) {
      clearTimeout(recoveryTimer);
      recoveryTimer = null;
    }
  }

  function openConnection() {
    return new Promise((resolve, reject) => {
      const connection = net.createConnection(daemonEndpoint);
      const cleanup = () => {
        connection.off("connect", onConnect);
        connection.off("error", onError);
      };
      const onConnect = () => {
        cleanup();
        resolve(connection);
      };
      const onError = (error) => {
        cleanup();
        connection.destroy();
        reject(error);
      };

      connection.once("connect", onConnect);
      connection.once("error", onError);
    });
  }

  function spawnDaemon() {
    const child = spawn(process.execPath, [daemonScriptPath], {
      detached: true,
      env: {
        ...process.env,
        REMOTE_TERMINAL_DAEMON_ENDPOINT: daemonEndpoint,
      },
      stdio: "ignore",
      windowsHide: true,
    });
    child.unref();
    daemonPid = child.pid;
  }

  async function connectAndAttach() {
    const connection = await openConnection();
    connection.setEncoding("utf8");
    socket = connection;

    await new Promise((resolve, reject) => {
      let settled = false;

      const settle = (callback, value) => {
        if (settled) {
          return;
        }
        settled = true;
        callback(value);
      };

      bindJsonLineMessages(connection, (message) => {
        if (message.type === "attached") {
          daemonConnected = true;
          daemonPid = message.daemonPid || daemonPid;
          settle(resolve, message);
          backend.emit("daemon-status", {
            daemonPid,
            state: "ready",
          });
          return;
        }

        if (message.type === "output") {
          backend.emit("output", message.data);
          return;
        }

        if (message.type === "session_exit") {
          backend.emit("session-exit", {
            exitCode: message.exitCode ?? null,
            signal: message.signal ?? null,
          });
          return;
        }

        if (message.type === "error") {
          backend.emit("error", new Error(message.message));
        }
      });

      connection.on("close", () => {
        if (socket === connection) {
          socket = null;
          daemonConnected = false;
          if (!stopping) {
            backend.emit("daemon-status", { state: "recovering" });
            scheduleRecovery();
          }
        }
        settle(reject, new Error("PTY daemon connection closed"));
      });

      connection.on("error", (error) => {
        if (!stopping) {
          backend.emit("error", error);
        }
      });

      sendJsonLine(connection, {
        cols: options.cols ?? DEFAULT_COLS,
        cwd: options.cwd,
        env: options.env,
        rows: options.rows ?? DEFAULT_ROWS,
        sessionId,
        shell: options.shell,
        type: "attach",
      });
    });
  }

  async function ensureConnection() {
    try {
      await connectAndAttach();
      return;
    } catch (_error) {
      spawnDaemon();
    }

    let lastError = null;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      try {
        await sleep(100);
        await connectAndAttach();
        return;
      } catch (error) {
        lastError = error;
      }
    }

    throw lastError || new Error("Unable to connect to the PTY daemon");
  }

  function scheduleRecovery() {
    if (recovering || recoveryTimer || stopping) {
      return;
    }

    recoveryTimer = setTimeout(async () => {
      recoveryTimer = null;
      recovering = true;
      let retryRecovery = false;
      try {
        await ensureConnection();
      } catch (error) {
        backend.emit("error", error);
        retryRecovery = !stopping;
      } finally {
        recovering = false;
        if (retryRecovery) {
          scheduleRecovery();
        }
      }
    }, daemonRestartDelayMs);
  }

  await ensureConnection();

  return {
    backendMode: "daemon",
    async close() {
      stopping = true;
      clearRecoveryTimer();
      if (socket) {
        socket.destroy();
        socket = null;
      }
      daemonConnected = false;
      if (terminateDaemonOnClose && daemonPid) {
        try {
          process.kill(daemonPid);
        } catch (_error) {
          // Process already exited.
        }
      }
    },
    get daemonConnected() {
      return daemonConnected;
    },
    get daemonEndpoint() {
      return daemonEndpoint;
    },
    get daemonPid() {
      return daemonPid;
    },
    off(eventName, listener) {
      backend.off(eventName, listener);
    },
    on(eventName, listener) {
      backend.on(eventName, listener);
    },
    get ptyProcess() {
      return null;
    },
    resize(cols, rows) {
      if (socket && daemonConnected) {
        sendJsonLine(socket, {
          cols,
          rows,
          sessionId,
          type: "resize",
        });
      }
    },
    get shellExit() {
      return null;
    },
    get shellRunning() {
      return daemonConnected;
    },
    write(chunk) {
      if (socket && daemonConnected && typeof chunk === "string") {
        sendJsonLine(socket, {
          data: chunk,
          sessionId,
          type: "input",
        });
      }
    },
  };
}

async function startRemoteTerminal(options = {}) {
  const logger = options.logger || console.log;
  const qrWriter = options.qrWriter;
  const host = options.host || process.env.REMOTE_TERMINAL_HOST || DEFAULT_HOST;
  const accessHost = pickAccessHost(options.accessHost || process.env.REMOTE_TERMINAL_ACCESS_HOST);
  const port = resolvePort(options.port);
  const now = options.now || Date.now;
  const accessToken = createAccessToken(options.token, {
    now,
    tokenTtlMs: options.tokenTtlMs,
  });
  const token = accessToken.value;
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
  const verifyTunnel = options.verifyTunnel || defaultVerifyTunnel;
  const retryBaseMs = options.retryBaseMs ?? DEFAULT_TUNNEL_RETRY_BASE_MS;
  const retryMaxMs = options.retryMaxMs ?? DEFAULT_TUNNEL_RETRY_MAX_MS;
  const verifyAttempts = options.verifyAttempts ?? DEFAULT_TUNNEL_VERIFY_ATTEMPTS;
  const verifyDelayMs = options.verifyDelayMs ?? DEFAULT_TUNNEL_VERIFY_DELAY_MS;
  const authRateLimitWindowMs = options.authRateLimitWindowMs ?? DEFAULT_AUTH_RATE_LIMIT_WINDOW_MS;
  const authRateLimitMaxAttempts = options.authRateLimitMaxAttempts ?? DEFAULT_AUTH_RATE_LIMIT_MAX_ATTEMPTS;
  const authRateLimiter = createAuthRateLimiter({
    maxAttempts: authRateLimitMaxAttempts,
    now,
    windowMs: authRateLimitWindowMs,
  });

  const app = express();
  app.disable("x-powered-by");

  let publicUrl = null;
  let tunnel = null;
  let stopPromise = null;
  let closedResolve;
  let shellRunning = true;
  let shellExit = null;
  let tunnelError = null;
  let tunnelState = TUNNEL_STATES.STOPPED;
  let tunnelAttempt = 0;
  let tunnelRetryDelayMs = null;
  let retryTimer = null;
  let verificationInFlight = false;
  let pendingVerification = null;
  let stopping = false;
  let activePort = null;
  let ptyBackend = null;
  const closed = new Promise((resolve) => {
    closedResolve = resolve;
  });
  const lastResize = {
    cols: DEFAULT_COLS,
    rows: DEFAULT_ROWS,
  };

  const server = http.createServer(app);
  const io = new Server(server, {
    serveClient: true,
  });
  const spinner = tunnelEnabled ? await createSpinner(options.spinnerFactory) : createNoopSpinner();

  function setPublicUrl(url) {
    publicUrl = url;
  }

  function setTunnelError(message) {
    tunnelError = message;
  }

  function updateSpinner(text, terminalAction) {
    if (!spinner) {
      return;
    }

    if (terminalAction === "succeed" && typeof spinner.succeed === "function") {
      if (!spinner.isSpinning && typeof spinner.start === "function") {
        spinner.start(text);
      }
      spinner.succeed(text);
      return;
    }

    if (terminalAction === "warn" && typeof spinner.warn === "function") {
      if (!spinner.isSpinning && typeof spinner.start === "function") {
        spinner.start(text);
      }
      spinner.warn(text);
      return;
    }

    if (!spinner.isSpinning && typeof spinner.start === "function") {
      spinner.start(text);
      return;
    }

    spinner.text = text;
  }

  function setTunnelState(nextState, detail, terminalAction = null) {
    tunnelState = nextState;
    const text = `[${nextState}] ${detail}`;
    logger(text);
    updateSpinner(text, terminalAction);
  }

  function clearRetryTimer() {
    if (retryTimer) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    tunnelRetryDelayMs = null;
  }

  function getRequestClientId(req) {
    const directAddress = req.socket && req.socket.remoteAddress;
    if (isLoopbackAddress(directAddress)) {
      const forwardedClientId = getForwardedClientId(req.headers);
      if (forwardedClientId) {
        return forwardedClientId;
      }
    }

    return req.ip || directAddress || "unknown";
  }

  function getSocketClientId(socket) {
    const directAddress =
      (socket.request && socket.request.socket && socket.request.socket.remoteAddress) || socket.handshake.address;
    if (isLoopbackAddress(directAddress)) {
      const forwardedClientId = getForwardedClientId(socket.handshake.headers);
      if (forwardedClientId) {
        return forwardedClientId;
      }
    }

    return socket.handshake.address || directAddress || "unknown";
  }

  function authenticateClient(clientId, candidateToken) {
    const rateLimitState = authRateLimiter.check(clientId);
    if (rateLimitState.limited) {
      return {
        ok: false,
        message: "too many auth attempts",
        retryAfterMs: rateLimitState.retryAfterMs,
        status: 429,
      };
    }

    const validation = validateAccessToken(accessToken, candidateToken, now);
    if (!validation.ok) {
      if (validation.reason === "invalid") {
        authRateLimiter.recordFailure(clientId);
      }
      return {
        ok: false,
        message: validation.message,
        status: 401,
      };
    }

    authRateLimiter.reset(clientId);
    return {
      ok: true,
    };
  }

  function sendAuthFailure(res, authResult) {
    if (authResult.retryAfterMs) {
      res.set("Retry-After", String(Math.max(1, Math.ceil(authResult.retryAfterMs / 1000))));
    }

    res.status(authResult.status).type("text/plain").send(authResult.message);
  }

  async function scheduleRetry(message) {
    if (stopping || !tunnelEnabled) {
      return;
    }

    clearRetryTimer();
    pendingVerification = null;

    if (tunnel) {
      tunnel.stop();
      tunnel = null;
    }

    const delayMs = Math.min(retryMaxMs, retryBaseMs * 2 ** Math.max(0, tunnelAttempt - 1));
    tunnelRetryDelayMs = delayMs;
    setTunnelState(TUNNEL_STATES.STOPPED, `${message}; retrying in ${formatRetryDelay(delayMs)}`, "warn");
    retryTimer = setTimeout(() => {
      retryTimer = null;
      tunnelRetryDelayMs = null;
      void connectTunnel();
    }, delayMs);
  }

  async function verifyAndMarkReady(publicAccessUrl, attemptNumber) {
    if (stopping || publicAccessUrl !== publicUrl || attemptNumber !== tunnelAttempt) {
      return;
    }

    if (verificationInFlight) {
      pendingVerification = { publicAccessUrl, attemptNumber };
      return;
    }

    verificationInFlight = true;
    pendingVerification = null;
    try {
      await verifyTunnel(publicAccessUrl, {
        attempts: verifyAttempts,
        delayMs: verifyDelayMs,
        onProgress: (attempt, total) => {
          setTunnelState(TUNNEL_STATES.VERIFYING, `Verifying public URL (${attempt}/${total})`);
        },
      });

      if (stopping || publicAccessUrl !== publicUrl || attemptNumber !== tunnelAttempt) {
        return;
      }

      clearRetryTimer();
      tunnelAttempt = 0;
      setTunnelError(null);
      setTunnelState(TUNNEL_STATES.READY, `Public URL ready: ${stripTokenFromUrl(publicAccessUrl)}`, "succeed");
    } catch (error) {
      if (stopping || publicAccessUrl !== publicUrl || attemptNumber !== tunnelAttempt) {
        return;
      }
      const message = error instanceof Error ? error.message : String(error);
      await scheduleRetry(`Tunnel verification failed: ${message}`);
    } finally {
      verificationInFlight = false;
      const nextVerification = pendingVerification;
      pendingVerification = null;
      if (
        !stopping &&
        nextVerification &&
        nextVerification.publicAccessUrl === publicUrl &&
        nextVerification.attemptNumber === tunnelAttempt
      ) {
        void verifyAndMarkReady(nextVerification.publicAccessUrl, nextVerification.attemptNumber);
      }
    }
  }

  async function connectTunnel() {
    if (stopping || !tunnelEnabled) {
      return;
    }

    clearRetryTimer();
    tunnelAttempt += 1;
    const attemptNumber = tunnelAttempt;
    const attemptLabel = `attempt ${tunnelAttempt}`;

    try {
      setTunnelState(TUNNEL_STATES.PREPARING, `Preparing Cloudflare Quick Tunnel (${attemptLabel})`);
      if (!options.tunnelFactory) {
        await ensureCloudflaredBinary(logger);
      }

      setTunnelState(TUNNEL_STATES.CONNECTING, `Starting Cloudflare Quick Tunnel (${attemptLabel})`);
      tunnel = createTunnel(`http://127.0.0.1:${activePort}`, token, {
        logger,
        qrWriter,
        setPublicUrl,
        setTunnelError,
        tunnelFactory: options.tunnelFactory || ((target) => Tunnel.quick(target)),
        onConnected: (connection) => {
          const location = connection && connection.location ? connection.location : "unknown";
          setTunnelState(TUNNEL_STATES.TUNNELING, `Tunnel connected via ${location}`);
        },
        onTunneling: (publicAccessUrl) => {
          setTunnelState(TUNNEL_STATES.TUNNELING, `Public URL issued: ${stripTokenFromUrl(publicAccessUrl)}`);
        },
        onReadyRequested: (publicAccessUrl) => {
          void verifyAndMarkReady(publicAccessUrl, attemptNumber);
        },
        onFailure: (message) => {
          void scheduleRetry(message);
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setTunnelError(message);
      await scheduleRetry(`Cloudflare Quick Tunnel unavailable: ${message}`);
    }
  }

  async function stop() {
    if (stopPromise) {
      return stopPromise;
    }

    stopPromise = (async () => {
      stopping = true;
      clearRetryTimer();
      if (tunnel) {
        tunnel.stop();
        tunnel = null;
      }

      if (ptyBackend) {
        shellRunning = false;
        await ptyBackend.close();
      }

      await Promise.allSettled([
        new Promise((resolve) => io.close(() => resolve())),
        new Promise((resolve) => server.close(() => resolve())),
      ]);

      if (spinner && typeof spinner.stop === "function") {
        spinner.stop();
      }

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
      tunnelState,
      tunnelAttempt,
      tunnelRetryDelayMs,
      tunnelError,
      shell: shell.command,
      shellArgs: shell.args,
      shellExit,
      lastResize,
      tunnelEnabled,
      backendMode: ptyBackend ? ptyBackend.backendMode : null,
      daemonConnected: ptyBackend ? ptyBackend.daemonConnected : null,
      daemonEndpoint: ptyBackend ? ptyBackend.daemonEndpoint : null,
      daemonPid: ptyBackend ? ptyBackend.daemonPid : null,
      persistentToken: accessToken.persistent,
      tokenExpiresAt: accessToken.expiresAt === null ? null : new Date(accessToken.expiresAt).toISOString(),
      authRateLimitMaxAttempts,
      authRateLimitWindowMs,
    });
  });

  app.get("/", (req, res) => {
    const providedToken = Array.isArray(req.query.token) ? req.query.token[0] : req.query.token;
    const authResult = authenticateClient(getRequestClientId(req), providedToken);
    if (!authResult.ok) {
      sendAuthFailure(res, authResult);
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
    const authResult = authenticateClient(getSocketClientId(socket), Array.isArray(providedToken) ? providedToken[0] : providedToken);
    if (!authResult.ok) {
      const error = new Error(
        authResult.status === 401 && authResult.message === "missing or invalid token"
          ? "unauthorized"
          : authResult.message,
      );
      if (authResult.retryAfterMs) {
        error.data = {
          retryAfterMs: authResult.retryAfterMs,
        };
      }
      next(error);
      return;
    }

    next();
  });

  io.on("connection", (socket) => {
    socket.emit("output", "");
    socket.on("input", (chunk) => {
      if (ptyBackend && typeof chunk === "string" && shellRunning) {
        ptyBackend.write(chunk);
      }
    });

    socket.on("resize", (payload) => {
      const cols = Number.parseInt(String(payload?.cols), 10);
      const rows = Number.parseInt(String(payload?.rows), 10);
      if (ptyBackend && Number.isInteger(cols) && Number.isInteger(rows) && cols > 0 && rows > 0) {
        lastResize.cols = cols;
        lastResize.rows = rows;
        ptyBackend.resize(cols, rows);
      }
    });
  });

  try {
    if (options.ptyFactory) {
      ptyBackend = createInlinePtyBackend({
        cols: DEFAULT_COLS,
        cwd,
        env,
        ptyFactory: options.ptyFactory,
        rows: DEFAULT_ROWS,
        shell,
      });
    }

    await listen(server, port, host);
  } catch (error) {
    if (ptyBackend) {
      shellRunning = false;
      await ptyBackend.close();
    }
    io.close();
    throw error;
  }

  activePort = server.address().port;
  if (!ptyBackend) {
    try {
      ptyBackend = await createDaemonPtyBackend({
        cols: DEFAULT_COLS,
        cwd,
        daemonEndpoint: options.daemonEndpoint,
        daemonRestartDelayMs: options.daemonRestartDelayMs,
        daemonScriptPath: options.daemonScriptPath,
        env,
        port: activePort,
        rows: DEFAULT_ROWS,
        sessionId: options.sessionId,
        shell,
        terminateDaemonOnClose: options.terminateDaemonOnStop ?? options.port === 0,
      });
    } catch (error) {
      io.close();
      await new Promise((resolve) => server.close(() => resolve()));
      throw error;
    }
  }

  ptyBackend.on("daemon-status", (status) => {
    if (status.state === "recovering") {
      logger(`PTY daemon disconnected; retrying in ${formatRetryDelay(options.daemonRestartDelayMs ?? DEFAULT_DAEMON_RESTART_DELAY_MS)}`);
      return;
    }

    if (status.state === "ready" && status.daemonPid) {
      logger(`PTY daemon ready via ${ptyBackend.daemonEndpoint} (pid=${status.daemonPid})`);
    }
  });
  ptyBackend.on("error", (error) => {
    logger(`PTY backend error: ${error.message}`);
  });
  ptyBackend.on("output", (data) => {
    io.emit("output", data);
  });
  ptyBackend.on("session-exit", ({ exitCode, signal }) => {
    shellRunning = false;
    shellExit = {
      exitCode,
      signal,
    };
    io.emit("session-exit", shellExit);
    logger(`Shell exited (code=${exitCode ?? "null"}, signal=${signal ?? "none"}).`);
    void stop();
  });

  const localUrl = buildAccessUrl(`http://${accessHost}:${activePort}/`, token);
  logger(`Remote terminal listening on http://${host}:${activePort}`);
  logger(`Shell: ${shell.command}${shell.args.length ? ` ${shell.args.join(" ")}` : ""}`);
  if (accessToken.persistent) {
    logger("Auth: persistent trusted-device token enabled");
  } else {
    logger(`Auth: one-time QR token expires at ${new Date(accessToken.expiresAt).toISOString()}`);
  }
  renderQrCode(localUrl, {
    label: "Local access QR",
    logger,
    qrWriter,
  });

  if (tunnelEnabled) {
    await connectTunnel();
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
    get persistentToken() {
      return accessToken.persistent;
    },
    get tokenExpiresAt() {
      return accessToken.expiresAt;
    },
    get tunnelState() {
      return tunnelState;
    },
    get tunnelRetryDelayMs() {
      return tunnelRetryDelayMs;
    },
    lastResize,
    closed,
    server,
    io,
    get daemonConnected() {
      return ptyBackend ? ptyBackend.daemonConnected : null;
    },
    get daemonEndpoint() {
      return ptyBackend ? ptyBackend.daemonEndpoint : null;
    },
    get daemonPid() {
      return ptyBackend ? ptyBackend.daemonPid : null;
    },
    get ptyProcess() {
      return ptyBackend ? ptyBackend.ptyProcess : null;
    },
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
  DEFAULT_AUTH_RATE_LIMIT_MAX_ATTEMPTS,
  DEFAULT_AUTH_RATE_LIMIT_WINDOW_MS,
  DEFAULT_AUTH_TOKEN_TTL_MS,
  DEFAULT_PORT,
  DEFAULT_TUNNEL_RETRY_BASE_MS,
  DEFAULT_TUNNEL_RETRY_MAX_MS,
  DEFAULT_TUNNEL_VERIFY_ATTEMPTS,
  DEFAULT_TUNNEL_VERIFY_DELAY_MS,
  TUNNEL_STATES,
  buildAccessUrl,
  defaultVerifyTunnel,
  formatRetryDelay,
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
