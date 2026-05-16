const fs = require("node:fs");
const net = require("node:net");

const pty = require("node-pty");

const DEFAULT_COLS = 120;
const DEFAULT_ROWS = 40;
const DEFAULT_TERM = "xterm-256color";
const endpoint = process.env.REMOTE_TERMINAL_DAEMON_ENDPOINT;

if (!endpoint) {
  throw new Error("REMOTE_TERMINAL_DAEMON_ENDPOINT is required");
}

const sessions = new Map();

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
        // Ignore malformed partial frames from controllers disconnecting mid-write.
      }
    }
  });
}

function broadcast(session, message) {
  for (const controller of session.controllers) {
    sendJsonLine(controller, message);
  }
}

function createSession(message) {
  const ptyProcess = pty.spawn(message.shell.command, message.shell.args || [], {
    cols: message.cols ?? DEFAULT_COLS,
    cwd: message.cwd,
    env: message.env,
    name: DEFAULT_TERM,
    rows: message.rows ?? DEFAULT_ROWS,
  });

  const session = {
    controllers: new Set(),
    lastResize: {
      cols: message.cols ?? DEFAULT_COLS,
      rows: message.rows ?? DEFAULT_ROWS,
    },
    ptyProcess,
    shellExit: null,
    shellRunning: true,
  };

  ptyProcess.onData((data) => {
    broadcast(session, {
      data,
      sessionId: message.sessionId,
      type: "output",
    });
  });

  ptyProcess.onExit(({ exitCode, signal }) => {
    session.shellExit = {
      exitCode,
      signal,
    };
    session.shellRunning = false;
    broadcast(session, {
      exitCode,
      sessionId: message.sessionId,
      signal,
      type: "session_exit",
    });
  });

  return session;
}

function detachController(socket) {
  for (const session of sessions.values()) {
    session.controllers.delete(socket);
  }
}

function handleControllerMessage(socket, message) {
  if (message.type === "attach") {
    const existing = sessions.get(message.sessionId);
    const reused = Boolean(existing && existing.shellRunning);
    const session = reused ? existing : createSession(message);
    sessions.set(message.sessionId, session);
    session.controllers.add(socket);
    sendJsonLine(socket, {
      daemonPid: process.pid,
      lastResize: session.lastResize,
      reused,
      sessionId: message.sessionId,
      shellExit: session.shellExit,
      type: "attached",
    });
    return;
  }

  const session = sessions.get(message.sessionId);
  if (!session || !session.shellRunning) {
    return;
  }

  if (message.type === "input" && typeof message.data === "string") {
    session.ptyProcess.write(message.data);
    return;
  }

  if (message.type === "resize") {
    const cols = Number.parseInt(String(message.cols), 10);
    const rows = Number.parseInt(String(message.rows), 10);
    if (Number.isInteger(cols) && Number.isInteger(rows) && cols > 0 && rows > 0) {
      session.lastResize = { cols, rows };
      session.ptyProcess.resize(cols, rows);
    }
  }
}

if (process.platform !== "win32" && fs.existsSync(endpoint)) {
  fs.unlinkSync(endpoint);
}

const server = net.createServer((socket) => {
  bindJsonLineMessages(socket, (message) => handleControllerMessage(socket, message));
  socket.on("close", () => {
    detachController(socket);
  });
});

server.on("error", () => {
  process.exit(1);
});

server.listen(endpoint);

function shutdown() {
  server.close(() => {
    if (process.platform !== "win32" && fs.existsSync(endpoint)) {
      fs.unlinkSync(endpoint);
    }
    process.exit(0);
  });
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
