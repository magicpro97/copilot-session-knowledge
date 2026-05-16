(function bootstrapRemoteTerminal() {
  const terminalNode = document.getElementById("terminal");
  const statusNode = document.getElementById("status");
  const search = new URLSearchParams(window.location.search);
  const token = search.get("token");

  if (!token) {
    statusNode.textContent = "Missing token. Re-open the QR URL from the operator terminal.";
    return;
  }

  const terminal = new window.Terminal({
    cursorBlink: true,
    convertEol: true,
    scrollback: 5000,
    theme: {
      background: "#050816",
      foreground: "#e2e8f0",
      cursor: "#7dd3fc",
    },
  });
  const fitAddon = new window.FitAddon.FitAddon();
  terminal.loadAddon(fitAddon);
  terminal.open(terminalNode);

  const socket = window.io({
    auth: { token },
    transports: ["websocket"],
  });

  function syncSize() {
    fitAddon.fit();
    socket.emit("resize", {
      cols: terminal.cols,
      rows: terminal.rows,
    });
  }

  socket.on("connect", function onConnect() {
    statusNode.textContent = "Connected";
    syncSize();
    terminal.focus();
  });

  socket.on("disconnect", function onDisconnect(reason) {
    statusNode.textContent = "Disconnected: " + reason;
  });

  socket.on("connect_error", function onConnectError(error) {
    statusNode.textContent = "Connection failed: " + error.message;
  });

  socket.on("output", function onOutput(data) {
    terminal.write(data);
  });

  socket.on("session-exit", function onSessionExit(payload) {
    const code = payload && payload.exitCode !== null ? payload.exitCode : "null";
    statusNode.textContent = "Shell exited (code=" + code + ")";
  });

  terminal.onData(function onInput(data) {
    socket.emit("input", data);
  });

  window.addEventListener("resize", syncSize);
})();
