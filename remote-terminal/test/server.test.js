const assert = require("node:assert/strict");
const { EventEmitter, once } = require("node:events");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { io } = require("socket.io-client");

const {
  DEFAULT_AUTH_RATE_LIMIT_MAX_ATTEMPTS,
  DEFAULT_AUTH_TOKEN_TTL_MS,
  DEFAULT_PORT,
  TUNNEL_STATES,
  buildAccessUrl,
  resolvePort,
  stripTokenFromUrl,
  startRemoteTerminal,
} = require("../server.js");

async function waitFor(check, message, timeout = 8000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    if (check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }

  throw new Error(message);
}

function createDaemonEndpoint(label) {
  const suffix = `${label}-${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  if (process.platform === "win32") {
    return `\\\\.\\pipe\\${suffix}`;
  }

  return path.join(os.tmpdir(), `${suffix}.sock`);
}

function createSpinnerRecorder(events = []) {
  return {
    isSpinning: false,
    _text: "",
    get text() {
      return this._text;
    },
    set text(value) {
      this._text = value;
      events.push(`text:${value}`);
    },
    start(text) {
      this.isSpinning = true;
      if (text) {
        this.text = text;
      }
      events.push(`start:${this.text}`);
      return this;
    },
    stop() {
      this.isSpinning = false;
      events.push(`stop:${this.text}`);
      return this;
    },
    succeed(text) {
      this.isSpinning = false;
      if (text) {
        this.text = text;
      }
      events.push(`succeed:${this.text}`);
      return this;
    },
    warn(text) {
      this.isSpinning = false;
      if (text) {
        this.text = text;
      }
      events.push(`warn:${this.text}`);
      return this;
    },
  };
}

async function connectClient(remoteTerminal, token) {
  const socket = io(`http://127.0.0.1:${remoteTerminal.port}`, {
    auth: { token },
    reconnection: false,
    transports: ["websocket"],
  });
  await once(socket, "connect");
  return socket;
}

test("resolvePort keeps the issue default", () => {
  assert.equal(resolvePort(undefined), DEFAULT_PORT);
});

test("health endpoint is public but the terminal page stays token gated", async (t) => {
  const qrCodes = [];
  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    disableTunnel: true,
    logger: () => {},
    qrWriter: (label, url) => qrCodes.push({ label, url }),
    token: "issue75-health-token",
    port: 0,
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  const health = await fetch(`http://127.0.0.1:${remoteTerminal.port}/health`);
  assert.equal(health.status, 200);
  const payload = await health.json();
  assert.equal(payload.ok, true);
  assert.equal(payload.port, remoteTerminal.port);
  assert.equal(payload.localOrigin, stripTokenFromUrl(remoteTerminal.localUrl));
  assert.equal(payload.publicOrigin, null);
  assert.equal(payload.tunnelState, TUNNEL_STATES.STOPPED);
  assert.equal(JSON.stringify(payload).includes("issue75-health-token"), false);

  const denied = await fetch(`http://127.0.0.1:${remoteTerminal.port}/`);
  assert.equal(denied.status, 401);

  const allowed = await fetch(remoteTerminal.localUrl);
  assert.equal(allowed.status, 200);
  assert.match(await allowed.text(), /Remote Terminal/);
  assert.deepEqual(qrCodes, [{ label: "Local access QR", url: remoteTerminal.localUrl }]);
});

test("invalid socket auth is rejected", async (t) => {
  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    disableTunnel: true,
    logger: () => {},
    token: "issue75-socket-token",
    port: 0,
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  const socket = io(`http://127.0.0.1:${remoteTerminal.port}`, {
    auth: { token: "wrong-token" },
    reconnection: false,
    transports: ["websocket"],
  });
  t.after(() => socket.close());

  const [error] = await once(socket, "connect_error");
  assert.match(error.message, /unauthorized/);
});

test("generated QR tokens expire for new page and socket auth", async (t) => {
  let nowValue = 1000;
  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    disableTunnel: true,
    logger: () => {},
    now: () => nowValue,
    port: 0,
    tokenTtlMs: 200,
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  const validPage = await fetch(remoteTerminal.localUrl);
  assert.equal(validPage.status, 200);

  nowValue += 201;

  const expiredPage = await fetch(remoteTerminal.localUrl);
  assert.equal(expiredPage.status, 401);
  assert.match(await expiredPage.text(), /expired/);

  const socket = io(`http://127.0.0.1:${remoteTerminal.port}`, {
    auth: { token: remoteTerminal.token },
    reconnection: false,
    transports: ["websocket"],
  });
  t.after(() => socket.close());

  const [error] = await once(socket, "connect_error");
  assert.match(error.message, /expired/);

  const health = await fetch(`http://127.0.0.1:${remoteTerminal.port}/health`);
  const payload = await health.json();
  assert.equal(payload.persistentToken, false);
  assert.equal(payload.tokenExpiresAt, new Date(remoteTerminal.tokenExpiresAt).toISOString());
});

test("expired tokens do not consume the auth rate-limit budget", async (t) => {
  let nowValue = 1500;
  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    authRateLimitMaxAttempts: 2,
    authRateLimitWindowMs: 1000,
    disableTunnel: true,
    logger: () => {},
    now: () => nowValue,
    port: 0,
    tokenTtlMs: 200,
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  nowValue += 201;

  for (let attempt = 0; attempt < 4; attempt += 1) {
    const expiredPage = await fetch(remoteTerminal.localUrl);
    assert.equal(expiredPage.status, 401);
    assert.match(await expiredPage.text(), /expired/);
  }
});

test("trusted-device tokens stay valid beyond the one-time QR TTL", async (t) => {
  let nowValue = 2000;
  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    disableTunnel: true,
    logger: () => {},
    now: () => nowValue,
    port: 0,
    token: "issue78-trusted-device-token",
    tokenTtlMs: 200,
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  nowValue += 1000;

  const allowed = await fetch(remoteTerminal.localUrl);
  assert.equal(allowed.status, 200);
  assert.equal(remoteTerminal.persistentToken, true);
  assert.equal(remoteTerminal.tokenExpiresAt, null);

  const health = await fetch(`http://127.0.0.1:${remoteTerminal.port}/health`);
  const payload = await health.json();
  assert.equal(payload.persistentToken, true);
  assert.equal(payload.tokenExpiresAt, null);
});

test("invalid auth attempts are rate limited per client", async (t) => {
  const authRateLimitWindowMs = 1000;
  let nowValue = 3000;
  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    authRateLimitMaxAttempts: DEFAULT_AUTH_RATE_LIMIT_MAX_ATTEMPTS,
    authRateLimitWindowMs,
    disableTunnel: true,
    logger: () => {},
    now: () => nowValue,
    port: 0,
    token: "issue78-rate-limit-token",
    tokenTtlMs: DEFAULT_AUTH_TOKEN_TTL_MS,
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  for (let attempt = 0; attempt < DEFAULT_AUTH_RATE_LIMIT_MAX_ATTEMPTS; attempt += 1) {
    const response = await fetch(`http://127.0.0.1:${remoteTerminal.port}/?token=wrong-token`);
    assert.equal(response.status, 401);
  }

  const limited = await fetch(`http://127.0.0.1:${remoteTerminal.port}/?token=wrong-token`);
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get("retry-after"), String(Math.ceil(authRateLimitWindowMs / 1000)));
  assert.match(await limited.text(), /too many auth attempts/);

  nowValue += authRateLimitWindowMs + 1;

  const recovered = await fetch(remoteTerminal.localUrl);
  assert.equal(recovered.status, 200);
});

test("forwarded tunnel client IPs keep auth rate limiting scoped per client", async (t) => {
  const authRateLimitWindowMs = 1000;
  let nowValue = 4000;
  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    authRateLimitMaxAttempts: DEFAULT_AUTH_RATE_LIMIT_MAX_ATTEMPTS,
    authRateLimitWindowMs,
    disableTunnel: true,
    logger: () => {},
    now: () => nowValue,
    port: 0,
    token: "issue78-forwarded-ip-token",
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  const blockedHeaders = { "CF-Connecting-IP": "198.51.100.10" };
  const allowedHeaders = { "CF-Connecting-IP": "198.51.100.11" };

  for (let attempt = 0; attempt < DEFAULT_AUTH_RATE_LIMIT_MAX_ATTEMPTS; attempt += 1) {
    const response = await fetch(`http://127.0.0.1:${remoteTerminal.port}/?token=wrong-token`, {
      headers: blockedHeaders,
    });
    assert.equal(response.status, 401);
  }

  const blockedClient = await fetch(`http://127.0.0.1:${remoteTerminal.port}/?token=wrong-token`, {
    headers: blockedHeaders,
  });
  assert.equal(blockedClient.status, 429);

  const differentForwardedClient = await fetch(`http://127.0.0.1:${remoteTerminal.port}/?token=wrong-token`, {
    headers: allowedHeaders,
  });
  assert.equal(differentForwardedClient.status, 401);
});

test("server restart reattaches to the same PTY daemon session", async (t) => {
  const daemonEndpoint = createDaemonEndpoint("issue76-restart");
  const firstServer = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    daemonEndpoint,
    disableTunnel: true,
    logger: () => {},
    port: 0,
    terminateDaemonOnStop: false,
    token: "issue76-restart-token",
  });
  t.after(async () => {
    if (firstServer.closed) {
      await firstServer.closed;
    }
  });

  const firstSocket = await connectClient(firstServer, "issue76-restart-token");
  t.after(() => firstSocket.close());

  let firstOutput = "";
  firstSocket.on("output", (chunk) => {
    firstOutput += chunk;
  });

  const longRunningCommand =
    process.platform === "win32"
      ? "Start-Sleep -Seconds 2; Write-Output 'ISSUE76_RESTART_STILL_RUNNING'"
      : "sleep 2; echo ISSUE76_RESTART_STILL_RUNNING";
  firstSocket.emit("input", `${longRunningCommand}\r`);
  await new Promise((resolve) => setTimeout(resolve, 250));

  const originalDaemonPid = firstServer.daemonPid;
  const reusedPort = firstServer.port;
  await firstServer.stop();

  const restartedServer = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    daemonEndpoint,
    disableTunnel: true,
    logger: () => {},
    port: reusedPort,
    terminateDaemonOnStop: true,
    token: "issue76-restart-token",
  });
  t.after(async () => {
    await restartedServer.stop();
  });

  assert.equal(restartedServer.daemonPid, originalDaemonPid);

  const restartedSocket = await connectClient(restartedServer, "issue76-restart-token");
  t.after(() => restartedSocket.close());

  let restartedOutput = "";
  restartedSocket.on("output", (chunk) => {
    restartedOutput += chunk;
  });

  await waitFor(
    () => restartedOutput.includes("ISSUE76_RESTART_STILL_RUNNING"),
    "restarted server did not reattach to the existing PTY session",
    7000,
  );
});

test("PTY daemon restarts after a crash and reattaches within the configured delay", async (t) => {
  const daemonEndpoint = createDaemonEndpoint("issue76-crash");
  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    daemonEndpoint,
    daemonRestartDelayMs: 200,
    disableTunnel: true,
    logger: () => {},
    port: 0,
    terminateDaemonOnStop: true,
    token: "issue76-crash-token",
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  const socket = await connectClient(remoteTerminal, "issue76-crash-token");
  t.after(() => socket.close());

  let output = "";
  socket.on("output", (chunk) => {
    output += chunk;
  });

  const originalDaemonPid = remoteTerminal.daemonPid;
  process.kill(originalDaemonPid);

  const recoveryCommand =
    process.platform === "win32" ? "Write-Output 'ISSUE76_DAEMON_RECOVERED'" : "echo ISSUE76_DAEMON_RECOVERED";
  const startedAt = Date.now();
  while (!output.includes("ISSUE76_DAEMON_RECOVERED") && Date.now() - startedAt < 6000) {
    socket.emit("input", `${recoveryCommand}\r`);
    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  assert.match(output, /ISSUE76_DAEMON_RECOVERED/, "server never reattached to the restarted PTY daemon");
});

test("pty output streams through Socket.IO and resize reaches the PTY", async (t) => {
  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    disableTunnel: true,
    logger: () => {},
    token: "issue75-io-token",
    port: 0,
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  const socket = await connectClient(remoteTerminal, "issue75-io-token");
  t.after(() => socket.close());

  let output = "";
  socket.on("output", (chunk) => {
    output += chunk;
  });

  socket.emit("resize", { cols: 100, rows: 32 });
  await waitFor(
    () => remoteTerminal.lastResize.cols === 100 && remoteTerminal.lastResize.rows === 32,
    "resize did not reach the PTY",
  );

  const echoCommand =
    process.platform === "win32" ? "Write-Output 'ISSUE75_READY'" : "echo ISSUE75_READY";

  socket.emit("input", `${echoCommand}\r`);
  await waitFor(() => output.includes("ISSUE75_READY"), "did not receive PTY output");
});

test(
  "Ctrl+C interrupts the active command on POSIX shells",
  { skip: process.platform === "win32" ? "Ctrl+C proof runs in Linux CI with bash." : false },
  async (t) => {
    const remoteTerminal = await startRemoteTerminal({
      accessHost: "127.0.0.1",
      disableTunnel: true,
      env: {
        PATH: process.env.PATH,
      },
      logger: () => {},
      token: "issue75-ctrlc-token",
      port: 0,
      shellCommand: "/bin/bash",
      shellArgs: ["-i"],
    });
    t.after(async () => {
      await remoteTerminal.stop();
    });

    const socket = await connectClient(remoteTerminal, "issue75-ctrlc-token");
    t.after(() => socket.close());

    let output = "";
    socket.on("output", (chunk) => {
      output += chunk;
    });

    socket.emit("input", "sleep 30\r");
    await new Promise((resolve) => setTimeout(resolve, 250));
    socket.emit("input", "\u0003");
    socket.emit("input", "echo ISSUE75_AFTER_CTRL_C\r");

    await waitFor(
      () => output.includes("ISSUE75_AFTER_CTRL_C"),
      "Ctrl+C did not interrupt the active command quickly enough",
      5000,
    );
  },
);

test("Cloudflare Quick Tunnel URLs become tokenized public access links", async (t) => {
  const qrCodes = [];
  const tunnel = new EventEmitter();
  let stopCalls = 0;
  tunnel.stop = () => {
    stopCalls += 1;
    return true;
  };

  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    logger: () => {},
    qrWriter: (label, url) => qrCodes.push({ label, url }),
    spinnerFactory: () => createSpinnerRecorder(),
    token: "issue75-tunnel-token",
    port: 0,
    tunnelFactory: () => tunnel,
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  tunnel.emit("url", "https://issue75.trycloudflare.com");

  await waitFor(() => remoteTerminal.publicUrl !== null, "public Quick Tunnel URL never arrived");
  assert.equal(
    remoteTerminal.publicUrl,
    buildAccessUrl("https://issue75.trycloudflare.com", "issue75-tunnel-token"),
  );
  assert.deepEqual(qrCodes[1], {
    label: "Quick tunnel QR",
    url: remoteTerminal.publicUrl,
  });

  await remoteTerminal.stop();
  assert.equal(stopCalls > 0, true);
});

test("tunnel state machine reaches READY and reports spinner progress", async (t) => {
  const spinnerEvents = [];
  const tunnel = new EventEmitter();
  tunnel.stop = () => true;

  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    logger: () => {},
    spinnerFactory: () => createSpinnerRecorder(spinnerEvents),
    token: "issue77-ready-token",
    port: 0,
    tunnelFactory: () => tunnel,
    verifyTunnel: async (_url, options) => {
      options.onProgress(1, 1);
    },
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  tunnel.emit("connected", { location: "test-edge" });
  tunnel.emit("url", "https://issue77.trycloudflare.com");

  await waitFor(() => remoteTerminal.tunnelState === TUNNEL_STATES.READY, "tunnel never reached READY");

  const health = await fetch(`http://127.0.0.1:${remoteTerminal.port}/health`);
  const payload = await health.json();
  assert.equal(payload.tunnelState, TUNNEL_STATES.READY);
  assert.equal(payload.publicOrigin, stripTokenFromUrl(remoteTerminal.publicUrl));

  const joined = spinnerEvents.join("\n");
  assert.match(joined, /\[PREPARING\]/);
  assert.match(joined, /\[CONNECTING\]/);
  assert.match(joined, /\[TUNNELING\]/);
  assert.match(joined, /\[VERIFYING\]/);
  assert.match(joined, /\[READY\]/);
});

test("disconnects schedule an automatic tunnel retry", async (t) => {
  const firstTunnel = new EventEmitter();
  firstTunnel.stop = () => true;
  const secondTunnel = new EventEmitter();
  secondTunnel.stop = () => true;

  const tunnels = [firstTunnel, secondTunnel];
  let tunnelCalls = 0;

  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    logger: () => {},
    retryBaseMs: 50,
    retryMaxMs: 50,
    spinnerFactory: () => createSpinnerRecorder(),
    token: "issue77-retry-token",
    port: 0,
    tunnelFactory: () => tunnels[tunnelCalls++],
    verifyTunnel: async (_url, options) => {
      options.onProgress(1, 1);
    },
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  firstTunnel.emit("connected", { location: "retry-edge" });
  firstTunnel.emit("url", "https://issue77-retry.trycloudflare.com");
  await waitFor(() => remoteTerminal.tunnelState === TUNNEL_STATES.READY, "first tunnel never reached READY");

  firstTunnel.emit("disconnected", { location: "retry-edge" });

  const health = await fetch(`http://127.0.0.1:${remoteTerminal.port}/health`);
  const payload = await health.json();
  assert.equal(payload.tunnelState, TUNNEL_STATES.STOPPED);
  assert.equal(payload.tunnelRetryDelayMs, 50);
  assert.equal(payload.tunnelError, "cloudflared disconnected (retry-edge)");

  await waitFor(() => tunnelCalls === 2, "retry did not start a new tunnel");
});

test("replacement tunnels re-queue verification without spawning extra retries", async (t) => {
  const firstTunnel = new EventEmitter();
  let firstStopCalls = 0;
  firstTunnel.stop = () => {
    firstStopCalls += 1;
    return true;
  };

  const secondTunnel = new EventEmitter();
  let secondStopCalls = 0;
  secondTunnel.stop = () => {
    secondStopCalls += 1;
    return true;
  };

  const tunnels = [firstTunnel, secondTunnel];
  const verifyCalls = [];
  let tunnelCalls = 0;
  let rejectFirstVerification;

  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    logger: () => {},
    retryBaseMs: 25,
    retryMaxMs: 25,
    spinnerFactory: () => createSpinnerRecorder(),
    token: "issue77-overlap-token",
    port: 0,
    tunnelFactory: () => tunnels[tunnelCalls++],
    verifyTunnel: async (publicAccessUrl, options) => {
      verifyCalls.push(publicAccessUrl);
      options.onProgress(1, 1);
      if (verifyCalls.length === 1) {
        await new Promise((_, reject) => {
          rejectFirstVerification = reject;
        });
      }
    },
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  firstTunnel.emit("connected", { location: "first-edge" });
  firstTunnel.emit("url", "https://issue77-first.trycloudflare.com");
  await waitFor(() => verifyCalls.length === 1, "first tunnel verification never started");

  firstTunnel.emit("disconnected", { location: "first-edge" });
  await waitFor(() => tunnelCalls === 2, "replacement tunnel was never started");

  secondTunnel.emit("connected", { location: "second-edge" });
  secondTunnel.emit("url", "https://issue77-second.trycloudflare.com");
  rejectFirstVerification(new Error("stale verification failed"));

  await waitFor(() => verifyCalls.length === 2, "replacement tunnel verification was not re-queued");
  await waitFor(() => remoteTerminal.tunnelState === TUNNEL_STATES.READY, "replacement tunnel never reached READY");
  await new Promise((resolve) => setTimeout(resolve, 80));

  assert.equal(firstStopCalls > 0, true);
  assert.equal(secondStopCalls, 0);
  assert.equal(tunnelCalls, 2);

  const health = await fetch(`http://127.0.0.1:${remoteTerminal.port}/health`);
  const payload = await health.json();
  assert.equal(payload.tunnelState, TUNNEL_STATES.READY);
  assert.equal(payload.tunnelAttempt, 0);
  assert.equal(payload.tunnelError, null);
  assert.equal(payload.publicOrigin, stripTokenFromUrl(remoteTerminal.publicUrl));
});

test("async tunnel errors clear the public origin and surface in health", async (t) => {
  const tunnel = new EventEmitter();
  tunnel.stop = () => true;

  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    logger: () => {},
    retryBaseMs: 60000,
    retryMaxMs: 60000,
    spinnerFactory: () => createSpinnerRecorder(),
    token: "issue75-async-tunnel-token",
    port: 0,
    tunnelFactory: () => tunnel,
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  tunnel.emit("url", "https://issue75.trycloudflare.com");
  await waitFor(() => remoteTerminal.publicUrl !== null, "public Quick Tunnel URL never arrived");

  tunnel.emit("error", new Error("async tunnel broke"));
  await waitFor(() => remoteTerminal.publicUrl === null, "async tunnel error did not clear the public URL");

  const health = await fetch(`http://127.0.0.1:${remoteTerminal.port}/health`);
  const payload = await health.json();
  assert.equal(payload.publicOrigin, null);
  assert.equal(payload.tunnelState, TUNNEL_STATES.STOPPED);
  assert.equal(payload.tunnelRetryDelayMs, 60000);
  assert.equal(payload.tunnelError, "async tunnel broke");
});

test("tunnel bootstrap failures stay explicit without crashing the local server", async (t) => {
  const logs = [];
  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    logger: (message) => logs.push(message),
    retryBaseMs: 60000,
    retryMaxMs: 60000,
    spinnerFactory: () => createSpinnerRecorder(),
    token: "issue75-tunnel-failure-token",
    port: 0,
    tunnelFactory: () => {
      throw new Error("fake tunnel bootstrap failure");
    },
  });
  t.after(async () => {
    await remoteTerminal.stop();
  });

  const health = await fetch(`http://127.0.0.1:${remoteTerminal.port}/health`);
  const payload = await health.json();
  assert.equal(payload.ok, true);
  assert.equal(payload.publicOrigin, null);
  assert.equal(payload.tunnelState, TUNNEL_STATES.STOPPED);
  assert.equal(payload.tunnelRetryDelayMs, 60000);
  assert.equal(payload.tunnelError, "fake tunnel bootstrap failure");
  assert.match(logs.join("\n"), /Cloudflare Quick Tunnel unavailable: fake tunnel bootstrap failure/);
});

test("listen failures kill the spawned PTY before rethrowing", async (t) => {
  const occupied = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    disableTunnel: true,
    logger: () => {},
    token: "issue75-port-owner",
    port: 0,
  });
  t.after(async () => {
    await occupied.stop();
  });

  let killed = false;
  const fakePty = {
    kill() {
      killed = true;
    },
    onData() {},
    onExit() {},
    resize() {},
    write() {},
  };

  await assert.rejects(
    startRemoteTerminal({
      accessHost: "127.0.0.1",
      disableTunnel: true,
      logger: () => {},
      token: "issue75-port-collision",
      port: occupied.port,
      ptyFactory: () => fakePty,
    }),
    /EADDRINUSE|address already in use/,
  );
  assert.equal(killed, true);
});

test(
  "Ctrl+D forwards EOF to the PTY process",
  { skip: process.platform === "win32" ? "Ctrl+D shell-exit proof is covered in Linux CI." : false },
  async (t) => {
    const remoteTerminal = await startRemoteTerminal({
      accessHost: "127.0.0.1",
      disableTunnel: true,
      logger: () => {},
      token: "issue75-eof-token",
      port: 0,
      shellCommand: "/bin/cat",
      shellArgs: [],
    });
    t.after(async () => {
      await remoteTerminal.stop();
    });

    const socket = await connectClient(remoteTerminal, "issue75-eof-token");
    t.after(() => socket.close());

    socket.emit("input", "ISSUE75_EOF\r");
    socket.emit("input", "\u0004");
    await waitFor(() => remoteTerminal.server.listening === false, "Ctrl+D did not close the server session");
  },
);

test(
  "tab completion still works because the browser sends raw PTY input",
  { skip: process.platform === "win32" ? "Tab-completion proof runs in Linux CI with bash." : false },
  async (t) => {
    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "issue75-tab-"));
    const helperCommand = path.join(tempDir, "issue75-tab-target");
    await fs.writeFile(helperCommand, "#!/bin/sh\necho ISSUE75_TAB_OK\n", { mode: 0o755 });
    await fs.chmod(helperCommand, 0o755);
    t.after(async () => {
      await fs.rm(tempDir, { force: true, recursive: true });
    });

    const remoteTerminal = await startRemoteTerminal({
      accessHost: "127.0.0.1",
      disableTunnel: true,
      env: {
        PATH: `${tempDir}:${process.env.PATH}`,
      },
      logger: () => {},
      token: "issue75-tab-token",
      port: 0,
      shellCommand: "/bin/bash",
      shellArgs: ["-i"],
    });
    t.after(async () => {
      await remoteTerminal.stop();
    });

    const socket = await connectClient(remoteTerminal, "issue75-tab-token");
    t.after(() => socket.close());

    let output = "";
    socket.on("output", (chunk) => {
      output += chunk;
    });

    socket.emit("input", "issue75-tab-ta\t\r");
    await waitFor(() => output.includes("ISSUE75_TAB_OK"), "tab completion never executed the completed command");
  },
);
