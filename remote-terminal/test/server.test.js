const assert = require("node:assert/strict");
const { EventEmitter, once } = require("node:events");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { io } = require("socket.io-client");

const {
  DEFAULT_PORT,
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

test("async tunnel errors clear the public origin and surface in health", async (t) => {
  const tunnel = new EventEmitter();
  tunnel.stop = () => true;

  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    logger: () => {},
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
  assert.equal(payload.tunnelError, "async tunnel broke");
});

test("tunnel bootstrap failures stay explicit without crashing the local server", async (t) => {
  const logs = [];
  const remoteTerminal = await startRemoteTerminal({
    accessHost: "127.0.0.1",
    logger: (message) => logs.push(message),
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
