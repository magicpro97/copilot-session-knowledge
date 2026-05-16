(function remoteTerminalClient(globalScope) {
  const THEME_STORAGE_KEY = "remote-terminal-theme";
  const KEYBOARD_VISIBILITY_STORAGE_KEY = "remote-terminal-keyboard-open";
  const THEME_DEFINITIONS = Object.freeze({
    default: {
      colorScheme: "dark",
      id: "default",
      label: "Default",
      ui: {
        accent: "#7dd3fc",
        appBg: "#050816",
        buttonBg: "rgba(15, 23, 42, 0.92)",
        buttonBorder: "rgba(125, 211, 252, 0.28)",
        buttonText: "#e2e8f0",
        danger: "#f87171",
        keyboardBg: "rgba(15, 23, 42, 0.95)",
        mutedText: "#94a3b8",
        panelBg: "rgba(15, 23, 42, 0.92)",
        panelBorder: "rgba(148, 163, 184, 0.18)",
        shadow: "0 18px 40px rgba(2, 6, 23, 0.35)",
        statusInfo: "#7dd3fc",
        statusOffline: "#fda4af",
        statusOnline: "#86efac",
        statusWarning: "#facc15",
        surfaceBg: "#020617",
      },
      xterm: {
        background: "#050816",
        black: "#0f172a",
        blue: "#7dd3fc",
        brightBlack: "#334155",
        brightBlue: "#bae6fd",
        brightCyan: "#67e8f9",
        brightGreen: "#86efac",
        brightMagenta: "#f9a8d4",
        brightRed: "#fda4af",
        brightWhite: "#f8fafc",
        brightYellow: "#fde68a",
        cursor: "#7dd3fc",
        cursorAccent: "#020617",
        cyan: "#22d3ee",
        foreground: "#e2e8f0",
        green: "#4ade80",
        magenta: "#f472b6",
        red: "#f87171",
        selectionBackground: "#1e293b",
        white: "#cbd5f5",
        yellow: "#facc15",
      },
    },
    light: {
      colorScheme: "light",
      id: "light",
      label: "Light",
      ui: {
        accent: "#2563eb",
        appBg: "#e2e8f0",
        buttonBg: "rgba(255, 255, 255, 0.92)",
        buttonBorder: "rgba(37, 99, 235, 0.2)",
        buttonText: "#0f172a",
        danger: "#dc2626",
        keyboardBg: "rgba(255, 255, 255, 0.95)",
        mutedText: "#475569",
        panelBg: "rgba(255, 255, 255, 0.94)",
        panelBorder: "rgba(148, 163, 184, 0.45)",
        shadow: "0 16px 36px rgba(148, 163, 184, 0.3)",
        statusInfo: "#2563eb",
        statusOffline: "#dc2626",
        statusOnline: "#15803d",
        statusWarning: "#b45309",
        surfaceBg: "#f8fafc",
      },
      xterm: {
        background: "#ffffff",
        black: "#1f2937",
        blue: "#2563eb",
        brightBlack: "#64748b",
        brightBlue: "#1d4ed8",
        brightCyan: "#0f766e",
        brightGreen: "#15803d",
        brightMagenta: "#a21caf",
        brightRed: "#dc2626",
        brightWhite: "#0f172a",
        brightYellow: "#a16207",
        cursor: "#2563eb",
        cursorAccent: "#ffffff",
        cyan: "#0f766e",
        foreground: "#0f172a",
        green: "#15803d",
        magenta: "#a21caf",
        red: "#dc2626",
        selectionBackground: "#bfdbfe",
        white: "#475569",
        yellow: "#a16207",
      },
    },
    dracula: {
      colorScheme: "dark",
      id: "dracula",
      label: "Dracula",
      ui: {
        accent: "#bd93f9",
        appBg: "#282a36",
        buttonBg: "rgba(68, 71, 90, 0.92)",
        buttonBorder: "rgba(189, 147, 249, 0.26)",
        buttonText: "#f8f8f2",
        danger: "#ff5555",
        keyboardBg: "rgba(40, 42, 54, 0.96)",
        mutedText: "#c0c4d6",
        panelBg: "rgba(40, 42, 54, 0.94)",
        panelBorder: "rgba(98, 114, 164, 0.55)",
        shadow: "0 18px 42px rgba(22, 24, 33, 0.45)",
        statusInfo: "#8be9fd",
        statusOffline: "#ff5555",
        statusOnline: "#50fa7b",
        statusWarning: "#f1fa8c",
        surfaceBg: "#1f2230",
      },
      xterm: {
        background: "#282a36",
        black: "#21222c",
        blue: "#bd93f9",
        brightBlack: "#6272a4",
        brightBlue: "#d6acff",
        brightCyan: "#a4ffff",
        brightGreen: "#69ff94",
        brightMagenta: "#ff92df",
        brightRed: "#ff6e6e",
        brightWhite: "#ffffff",
        brightYellow: "#ffffa5",
        cursor: "#ff79c6",
        cursorAccent: "#282a36",
        cyan: "#8be9fd",
        foreground: "#f8f8f2",
        green: "#50fa7b",
        magenta: "#ff79c6",
        red: "#ff5555",
        selectionBackground: "#44475a",
        white: "#f8f8f2",
        yellow: "#f1fa8c",
      },
    },
    monokai: {
      colorScheme: "dark",
      id: "monokai",
      label: "Monokai",
      ui: {
        accent: "#a6e22e",
        appBg: "#1f1f1b",
        buttonBg: "rgba(39, 40, 34, 0.94)",
        buttonBorder: "rgba(166, 226, 46, 0.2)",
        buttonText: "#f8f8f2",
        danger: "#f92672",
        keyboardBg: "rgba(31, 31, 27, 0.97)",
        mutedText: "#c7c7bd",
        panelBg: "rgba(39, 40, 34, 0.95)",
        panelBorder: "rgba(117, 113, 94, 0.46)",
        shadow: "0 18px 42px rgba(9, 9, 6, 0.45)",
        statusInfo: "#66d9ef",
        statusOffline: "#f92672",
        statusOnline: "#a6e22e",
        statusWarning: "#fd971f",
        surfaceBg: "#11110f",
      },
      xterm: {
        background: "#272822",
        black: "#272822",
        blue: "#66d9ef",
        brightBlack: "#75715e",
        brightBlue: "#66d9ef",
        brightCyan: "#a1efe4",
        brightGreen: "#a6e22e",
        brightMagenta: "#ae81ff",
        brightRed: "#f92672",
        brightWhite: "#f9f8f5",
        brightYellow: "#fd971f",
        cursor: "#f8f8f0",
        cursorAccent: "#272822",
        cyan: "#a1efe4",
        foreground: "#f8f8f2",
        green: "#a6e22e",
        magenta: "#ae81ff",
        red: "#f92672",
        selectionBackground: "#49483e",
        white: "#f8f8f2",
        yellow: "#fd971f",
      },
    },
    "solarized-dark": {
      colorScheme: "dark",
      id: "solarized-dark",
      label: "Solarized Dark",
      ui: {
        accent: "#2aa198",
        appBg: "#002b36",
        buttonBg: "rgba(7, 54, 66, 0.94)",
        buttonBorder: "rgba(42, 161, 152, 0.26)",
        buttonText: "#eee8d5",
        danger: "#dc322f",
        keyboardBg: "rgba(0, 43, 54, 0.97)",
        mutedText: "#93a1a1",
        panelBg: "rgba(7, 54, 66, 0.95)",
        panelBorder: "rgba(88, 110, 117, 0.5)",
        shadow: "0 18px 40px rgba(0, 24, 31, 0.4)",
        statusInfo: "#268bd2",
        statusOffline: "#dc322f",
        statusOnline: "#859900",
        statusWarning: "#b58900",
        surfaceBg: "#001f27",
      },
      xterm: {
        background: "#002b36",
        black: "#073642",
        blue: "#268bd2",
        brightBlack: "#586e75",
        brightBlue: "#839496",
        brightCyan: "#93a1a1",
        brightGreen: "#586e75",
        brightMagenta: "#6c71c4",
        brightRed: "#cb4b16",
        brightWhite: "#fdf6e3",
        brightYellow: "#657b83",
        cursor: "#2aa198",
        cursorAccent: "#002b36",
        cyan: "#2aa198",
        foreground: "#eee8d5",
        green: "#859900",
        magenta: "#d33682",
        red: "#dc322f",
        selectionBackground: "#073642",
        white: "#eee8d5",
        yellow: "#b58900",
      },
    },
    "solarized-light": {
      colorScheme: "light",
      id: "solarized-light",
      label: "Solarized Light",
      ui: {
        accent: "#268bd2",
        appBg: "#fdf6e3",
        buttonBg: "rgba(255, 251, 235, 0.94)",
        buttonBorder: "rgba(38, 139, 210, 0.2)",
        buttonText: "#073642",
        danger: "#dc322f",
        keyboardBg: "rgba(253, 246, 227, 0.97)",
        mutedText: "#586e75",
        panelBg: "rgba(255, 251, 235, 0.95)",
        panelBorder: "rgba(147, 161, 161, 0.42)",
        shadow: "0 16px 36px rgba(147, 161, 161, 0.28)",
        statusInfo: "#268bd2",
        statusOffline: "#dc322f",
        statusOnline: "#859900",
        statusWarning: "#b58900",
        surfaceBg: "#eee8d5",
      },
      xterm: {
        background: "#fdf6e3",
        black: "#073642",
        blue: "#268bd2",
        brightBlack: "#657b83",
        brightBlue: "#268bd2",
        brightCyan: "#2aa198",
        brightGreen: "#859900",
        brightMagenta: "#6c71c4",
        brightRed: "#cb4b16",
        brightWhite: "#002b36",
        brightYellow: "#586e75",
        cursor: "#268bd2",
        cursorAccent: "#fdf6e3",
        cyan: "#2aa198",
        foreground: "#073642",
        green: "#859900",
        magenta: "#d33682",
        red: "#dc322f",
        selectionBackground: "#eee8d5",
        white: "#586e75",
        yellow: "#b58900",
      },
    },
  });

  const VIRTUAL_KEY_ROWS = Object.freeze([
    Object.freeze([
      { id: "ctrl", kind: "modifier", label: "Ctrl" },
      { id: "alt", kind: "modifier", label: "Alt" },
      { id: "shift", kind: "modifier", label: "Shift" },
      { id: "esc", kind: "sequence", label: "Esc" },
    ]),
    Object.freeze([
      { id: "tab", kind: "sequence", label: "Tab" },
      { id: "left", kind: "sequence", label: "Left" },
      { id: "up", kind: "sequence", label: "Up" },
      { id: "down", kind: "sequence", label: "Down" },
      { id: "right", kind: "sequence", label: "Right" },
      { id: "enter", kind: "sequence", label: "Enter" },
      { id: "backspace", kind: "sequence", label: "Bksp" },
    ]),
    Object.freeze([
      { id: "f1", kind: "sequence", label: "F1" },
      { id: "f2", kind: "sequence", label: "F2" },
      { id: "f3", kind: "sequence", label: "F3" },
      { id: "f4", kind: "sequence", label: "F4" },
    ]),
    Object.freeze([
      { id: "f5", kind: "sequence", label: "F5" },
      { id: "f6", kind: "sequence", label: "F6" },
      { id: "f7", kind: "sequence", label: "F7" },
      { id: "f8", kind: "sequence", label: "F8" },
    ]),
    Object.freeze([
      { id: "f9", kind: "sequence", label: "F9" },
      { id: "f10", kind: "sequence", label: "F10" },
      { id: "f11", kind: "sequence", label: "F11" },
      { id: "f12", kind: "sequence", label: "F12" },
    ]),
    Object.freeze([
      { id: "a", kind: "character", label: "A" },
      { id: "c", kind: "character", label: "C" },
      { id: "d", kind: "character", label: "D" },
      { id: "l", kind: "character", label: "L" },
      { id: "u", kind: "character", label: "U" },
      { id: "z", kind: "character", label: "Z" },
    ]),
  ]);

  const FUNCTION_KEY_SEQUENCES = Object.freeze({
    f1: "\u001bOP",
    f2: "\u001bOQ",
    f3: "\u001bOR",
    f4: "\u001bOS",
    f5: "\u001b[15~",
    f6: "\u001b[17~",
    f7: "\u001b[18~",
    f8: "\u001b[19~",
    f9: "\u001b[20~",
    f10: "\u001b[21~",
    f11: "\u001b[23~",
    f12: "\u001b[24~",
  });

  const ARROW_SUFFIXES = Object.freeze({
    down: "B",
    left: "D",
    right: "C",
    up: "A",
  });

  function readStoredValue(key) {
    try {
      return globalScope.localStorage ? globalScope.localStorage.getItem(key) : null;
    } catch (error) {
      console.warn("remote-terminal storage read failed:", error.message);
      return null;
    }
  }

  function writeStoredValue(key, value) {
    try {
      if (globalScope.localStorage) {
        globalScope.localStorage.setItem(key, value);
      }
    } catch (error) {
      console.warn("remote-terminal storage write failed:", error.message);
    }
  }

  function normalizeThemeName(themeName) {
    return Object.prototype.hasOwnProperty.call(THEME_DEFINITIONS, themeName) ? themeName : "default";
  }

  function getThemeDefinition(themeName) {
    return THEME_DEFINITIONS[normalizeThemeName(themeName)];
  }

  function getModifierCode(modifiers) {
    const code =
      1 + (modifiers.shift ? 1 : 0) + (modifiers.alt ? 2 : 0) + (modifiers.ctrl ? 4 : 0);
    return code === 1 ? null : code;
  }

  function prefixAlt(sequence, modifiers) {
    return modifiers.alt ? "\u001b" + sequence : sequence;
  }

  function encodeCharacter(value, modifiers) {
    let character = modifiers.shift ? value.toUpperCase() : value.toLowerCase();
    if (modifiers.ctrl) {
      const controlCode = character.toUpperCase().charCodeAt(0) - 64;
      if (controlCode > 0 && controlCode < 32) {
        character = String.fromCharCode(controlCode);
      }
    }
    return prefixAlt(character, modifiers);
  }

  function getVirtualKeySequence(keyId, modifiers) {
    const normalizedModifiers = {
      alt: Boolean(modifiers && modifiers.alt),
      ctrl: Boolean(modifiers && modifiers.ctrl),
      shift: Boolean(modifiers && modifiers.shift),
    };

    if (Object.prototype.hasOwnProperty.call(ARROW_SUFFIXES, keyId)) {
      const modifierCode = getModifierCode(normalizedModifiers);
      const sequence = modifierCode
        ? "\u001b[1;" + modifierCode + ARROW_SUFFIXES[keyId]
        : "\u001b[" + ARROW_SUFFIXES[keyId];
      return sequence;
    }

    if (Object.prototype.hasOwnProperty.call(FUNCTION_KEY_SEQUENCES, keyId)) {
      return prefixAlt(FUNCTION_KEY_SEQUENCES[keyId], normalizedModifiers);
    }

    switch (keyId) {
      case "tab":
        return prefixAlt(normalizedModifiers.shift ? "\u001b[Z" : "\t", normalizedModifiers);
      case "enter":
        return prefixAlt("\r", normalizedModifiers);
      case "backspace":
        return prefixAlt("\u007f", normalizedModifiers);
      case "esc":
        return "\u001b";
      default:
        if (/^[a-z]$/i.test(keyId)) {
          return encodeCharacter(keyId, normalizedModifiers);
        }
        return "";
    }
  }

  function applyThemeDefinition(rootNode, themeDefinition) {
    const variables = {
      "--accent": themeDefinition.ui.accent,
      "--app-bg": themeDefinition.ui.appBg,
      "--button-bg": themeDefinition.ui.buttonBg,
      "--button-border": themeDefinition.ui.buttonBorder,
      "--button-text": themeDefinition.ui.buttonText,
      "--danger": themeDefinition.ui.danger,
      "--keyboard-bg": themeDefinition.ui.keyboardBg,
      "--muted-text": themeDefinition.ui.mutedText,
      "--panel-bg": themeDefinition.ui.panelBg,
      "--panel-border": themeDefinition.ui.panelBorder,
      "--shadow": themeDefinition.ui.shadow,
      "--status-info": themeDefinition.ui.statusInfo,
      "--status-offline": themeDefinition.ui.statusOffline,
      "--status-online": themeDefinition.ui.statusOnline,
      "--status-warning": themeDefinition.ui.statusWarning,
      "--surface-bg": themeDefinition.ui.surfaceBg,
    };

    Object.keys(variables).forEach(function setVariable(name) {
      rootNode.style.setProperty(name, variables[name]);
    });
    rootNode.dataset.theme = themeDefinition.id;
    rootNode.style.colorScheme = themeDefinition.colorScheme;
  }

  function isTouchDevice(scope) {
    if (!scope || typeof scope !== "object") {
      return false;
    }

    if (typeof scope.matchMedia === "function" && scope.matchMedia("(pointer: coarse)").matches) {
      return true;
    }

    const navigatorValue = scope.navigator || {};
    return Boolean("ontouchstart" in scope || navigatorValue.maxTouchPoints > 0);
  }

  function setStatus(node, message, tone) {
    node.textContent = message;
    node.dataset.tone = tone;
  }

  function populateThemeSelect(selectNode, selectedTheme) {
    Object.keys(THEME_DEFINITIONS).forEach(function addThemeOption(themeName) {
      const theme = THEME_DEFINITIONS[themeName];
      const option = document.createElement("option");
      option.value = theme.id;
      option.textContent = theme.label;
      option.selected = theme.id === selectedTheme;
      selectNode.appendChild(option);
    });
  }

  function bootstrapRemoteTerminal() {
    if (typeof document === "undefined" || typeof window === "undefined") {
      return null;
    }

    const terminalNode = document.getElementById("terminal");
    const statusNode = document.getElementById("status");
    const themeSelectNode = document.getElementById("theme-select");
    const keyboardToggleNode = document.getElementById("keyboard-toggle");
    const keyboardPanelNode = document.getElementById("mobile-keyboard");
    const keyboardRowsNode = document.getElementById("keyboard-rows");
    const keyboardHintNode = document.getElementById("keyboard-hint");
    const search = new URLSearchParams(window.location.search);
    const token = search.get("token");

    if (!token) {
      setStatus(statusNode, "Missing token. Re-open the QR URL from the operator terminal.", "error");
      return null;
    }

    const savedThemeName = normalizeThemeName(readStoredValue(THEME_STORAGE_KEY));
    populateThemeSelect(themeSelectNode, savedThemeName);

    const terminal = new window.Terminal({
      cursorBlink: true,
      convertEol: true,
      scrollback: 5000,
      theme: getThemeDefinition(savedThemeName).xterm,
    });
    const fitAddon = new window.FitAddon.FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(terminalNode);
    terminal.focus();

    applyThemeDefinition(document.documentElement, getThemeDefinition(savedThemeName));

    const socket = window.io({
      auth: { token: token },
      transports: ["websocket"],
    });

    function syncSize() {
      fitAddon.fit();
      socket.emit("resize", {
        cols: terminal.cols,
        rows: terminal.rows,
      });
    }

    function applyTheme(themeName) {
      const themeDefinition = getThemeDefinition(themeName);
      applyThemeDefinition(document.documentElement, themeDefinition);
      if (typeof terminal.setOption === "function") {
        terminal.setOption("theme", themeDefinition.xterm);
      } else {
        terminal.options.theme = themeDefinition.xterm;
      }
      writeStoredValue(THEME_STORAGE_KEY, themeDefinition.id);
      syncSize();
    }

    themeSelectNode.addEventListener("change", function onThemeChange(event) {
      applyTheme(event.target.value);
    });

    const modifierState = {
      alt: false,
      ctrl: false,
      shift: false,
    };
    const modifierButtons = new Map();

    function syncModifierButtons() {
      modifierButtons.forEach(function setPressedState(buttonNode, modifierName) {
        buttonNode.setAttribute("aria-pressed", String(Boolean(modifierState[modifierName])));
      });
    }

    function resetModifiers() {
      modifierState.alt = false;
      modifierState.ctrl = false;
      modifierState.shift = false;
      syncModifierButtons();
      keyboardHintNode.textContent = "Touch shortcuts stay focused on the real PTY. Modifiers apply to the next tap.";
    }

    function updateKeyboardVisibility(open) {
      document.body.classList.toggle("keyboard-open", open);
      keyboardPanelNode.hidden = !open;
      keyboardToggleNode.setAttribute("aria-expanded", String(open));
      keyboardToggleNode.textContent = open ? "Hide keyboard" : "Show keyboard";
      writeStoredValue(KEYBOARD_VISIBILITY_STORAGE_KEY, open ? "1" : "0");
      if (open) {
        terminal.focus();
        syncSize();
      }
    }

    const storedKeyboardVisibility = readStoredValue(KEYBOARD_VISIBILITY_STORAGE_KEY);
    const keyboardOpenByDefault =
      storedKeyboardVisibility === "1"
        ? true
        : storedKeyboardVisibility === "0"
          ? false
          : isTouchDevice(globalScope);
    document.body.classList.toggle("touch-device", isTouchDevice(globalScope));

    keyboardToggleNode.addEventListener("click", function onKeyboardToggle() {
      updateKeyboardVisibility(keyboardPanelNode.hidden);
    });

    VIRTUAL_KEY_ROWS.forEach(function renderKeyboardRow(row) {
      const rowNode = document.createElement("div");
      rowNode.className = "keyboard-row";

      row.forEach(function renderKeyboardKey(key) {
        const buttonNode = document.createElement("button");
        buttonNode.type = "button";
        buttonNode.className = "keyboard-key";
        buttonNode.dataset.key = key.id;
        buttonNode.textContent = key.label;
        buttonNode.setAttribute("aria-label", key.label);

        if (key.kind === "modifier") {
          buttonNode.classList.add("keyboard-key--modifier");
          modifierButtons.set(key.id, buttonNode);
        } else if (key.id === "enter" || key.id === "backspace") {
          buttonNode.classList.add("keyboard-key--wide");
        }

        buttonNode.addEventListener("click", function onVirtualKeyPress() {
          if (key.kind === "modifier") {
            modifierState[key.id] = !modifierState[key.id];
            syncModifierButtons();
            keyboardHintNode.textContent =
              "Armed: " +
              Object.keys(modifierState)
                .filter(function activeModifier(modifierName) {
                  return modifierState[modifierName];
                })
                .map(function formatModifier(modifierName) {
                  return modifierName.toUpperCase();
                })
                .join(" + ");
            if (!modifierState.alt && !modifierState.ctrl && !modifierState.shift) {
              keyboardHintNode.textContent =
                "Touch shortcuts stay focused on the real PTY. Modifiers apply to the next tap.";
            }
            return;
          }

          const sequence = getVirtualKeySequence(key.id, modifierState);
          if (sequence) {
            socket.emit("input", sequence);
            terminal.focus();
          }
          resetModifiers();
        });

        rowNode.appendChild(buttonNode);
      });

      keyboardRowsNode.appendChild(rowNode);
    });

    updateKeyboardVisibility(keyboardOpenByDefault);
    syncModifierButtons();

    socket.on("connect", function onConnect() {
      setStatus(statusNode, "Connected", "success");
      syncSize();
      terminal.focus();
    });

    socket.on("disconnect", function onDisconnect(reason) {
      setStatus(statusNode, "Disconnected: " + reason, "warning");
    });

    socket.on("connect_error", function onConnectError(error) {
      setStatus(statusNode, "Connection failed: " + error.message, "error");
    });

    socket.on("output", function onOutput(data) {
      terminal.write(data);
    });

    socket.on("session-exit", function onSessionExit(payload) {
      const code = payload && payload.exitCode !== null ? payload.exitCode : "null";
      setStatus(statusNode, "Shell exited (code=" + code + ")", "error");
    });

    terminal.onData(function onInput(data) {
      socket.emit("input", data);
    });

    window.addEventListener("resize", syncSize);

    return {
      applyTheme: applyTheme,
      socket: socket,
      terminal: terminal,
      updateKeyboardVisibility: updateKeyboardVisibility,
    };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      KEYBOARD_VISIBILITY_STORAGE_KEY,
      THEME_DEFINITIONS,
      THEME_STORAGE_KEY,
      VIRTUAL_KEY_ROWS,
      bootstrapRemoteTerminal,
      getThemeDefinition,
      getVirtualKeySequence,
      normalizeThemeName,
    };
  }

  if (typeof window !== "undefined" && window.document) {
    bootstrapRemoteTerminal();
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
