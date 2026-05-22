import "@testing-library/jest-dom";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import {
  DiagnosticPanel,
  detectBrowser,
  detectBrowserInfo,
  browserSupportsPna,
  getPnaDocsUrl,
} from "./index";

// ── Browser detection helpers ─────────────────────────────────────────────────

describe("detectBrowser", () => {
  it("detects firefox from UA", () => {
    vi.stubGlobal("navigator", { userAgent: "Mozilla/5.0 Firefox/120.0" });
    expect(detectBrowser()).toBe("firefox");
    vi.unstubAllGlobals();
  });

  it("detects safari (not chrome) from UA", () => {
    vi.stubGlobal("navigator", {
      userAgent:
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Version/17.0 Safari/537.36",
    });
    expect(detectBrowser()).toBe("safari");
    vi.unstubAllGlobals();
  });

  it("detects chromium from Chrome UA", () => {
    vi.stubGlobal("navigator", {
      userAgent:
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    });
    expect(detectBrowser()).toBe("chromium");
    vi.unstubAllGlobals();
  });

  it("returns other for unknown UA", () => {
    vi.stubGlobal("navigator", { userAgent: "SpecialRobot/1.0" });
    expect(detectBrowser()).toBe("other");
    vi.unstubAllGlobals();
  });

  it("returns other when navigator is undefined (SSR)", () => {
    vi.stubGlobal("navigator", undefined);
    expect(detectBrowser()).toBe("other");
    vi.unstubAllGlobals();
  });
});

describe("detectBrowserInfo", () => {
  it("extracts chrome version from UA", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 Chrome/130.0.0.0 Safari/537.36",
    });
    const info = detectBrowserInfo();
    expect(info.kind).toBe("chromium");
    expect(info.version).toBe(130);
    vi.unstubAllGlobals();
  });

  it("extracts Edge (Chromium) version from UA", () => {
    vi.stubGlobal("navigator", {
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    });
    const info = detectBrowserInfo();
    expect(info.kind).toBe("chromium");
    // Edg/ takes precedence in the version regex
    expect(info.version).toBeGreaterThanOrEqual(126);
    vi.unstubAllGlobals();
  });

  it("extracts firefox version from UA", () => {
    vi.stubGlobal("navigator", { userAgent: "Mozilla/5.0 Firefox/121.0" });
    const info = detectBrowserInfo();
    expect(info.kind).toBe("firefox");
    expect(info.version).toBe(121);
    vi.unstubAllGlobals();
  });

  it("extracts safari version from UA", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Version/17.2 Safari/537.36",
    });
    const info = detectBrowserInfo();
    expect(info.kind).toBe("safari");
    expect(info.version).toBe(17);
    vi.unstubAllGlobals();
  });

  it("returns null version when navigator is undefined (SSR)", () => {
    vi.stubGlobal("navigator", undefined);
    const info = detectBrowserInfo();
    expect(info.kind).toBe("other");
    expect(info.version).toBeNull();
    vi.unstubAllGlobals();
  });
});

describe("browserSupportsPna", () => {
  it("returns true for chromium", () => expect(browserSupportsPna("chromium")).toBe(true));
  it("returns false for safari", () => expect(browserSupportsPna("safari")).toBe(false));
  it("returns false for firefox", () => expect(browserSupportsPna("firefox")).toBe(false));
  it("returns false for other", () => expect(browserSupportsPna("other")).toBe(false));
});

describe("getPnaDocsUrl", () => {
  it("returns LNA url for Chrome 126+", () => {
    const url = getPnaDocsUrl({ kind: "chromium", version: 130 });
    expect(url).toContain("local-network-access");
  });

  it("returns PNA url for Chrome < 126", () => {
    const url = getPnaDocsUrl({ kind: "chromium", version: 122 });
    expect(url).toContain("private-network-access");
    expect(url).not.toContain("local-network-access");
  });

  it("returns webkit url for safari", () => {
    const url = getPnaDocsUrl({ kind: "safari", version: 17 });
    expect(url).toContain("webkit.org");
  });

  it("returns bugzilla url for firefox", () => {
    const url = getPnaDocsUrl({ kind: "firefox", version: 121 });
    expect(url).toContain("bugzilla.mozilla.org");
  });

  it("returns PNA url for unknown browser", () => {
    const url = getPnaDocsUrl({ kind: "other", version: null });
    expect(url).toContain("private-network-access");
  });
});

// ── DiagnosticPanel rendering ─────────────────────────────────────────────────

describe("DiagnosticPanel", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders mixed-content-http panel for incompatible non-loopback host", () => {
    render(
      <DiagnosticPanel
        compat={{ compatible: false, code: "mixed-content-http", reason: "test" }}
        isHosted={true}
        hostUrl="http://192.168.1.10:8765"
      />
    );
    const panel = screen.getByTestId("diagnostic-panel");
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveAttribute("role", "alert");
    expect(panel).toHaveTextContent(/Mixed content/);
    expect(panel).toHaveTextContent(/HTTPS tunnel/);
    expect(panel).toHaveTextContent(/192\.168\.1\.10:8765/);
  });

  it("mixed-content panel contains copy buttons for code snippets", () => {
    render(
      <DiagnosticPanel
        compat={{ compatible: false, code: "mixed-content-http", reason: "test" }}
        isHosted={true}
        hostUrl="http://192.168.1.10:8765"
      />
    );
    const copyButtons = screen.getAllByRole("button", { name: /copy/i });
    expect(copyButtons.length).toBeGreaterThan(0);
  });

  it("renders pna-required panel (chromium — PNA supported)", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 Chrome/122.0.0.0 Safari/537.36",
    });
    render(
      <DiagnosticPanel
        compat={{ compatible: true, code: "pna-required", reason: "needs PNA" }}
        isHosted={true}
        hostUrl="http://localhost:8765"
      />
    );
    const panel = screen.getByTestId("diagnostic-panel");
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveTextContent(/Private Network Access/);
    expect(panel).toHaveTextContent(/--hosted-bootstrap/);
    // Chromium path: shows hosted-bootstrap instructions
    expect(panel).toHaveTextContent(/Chrome.*Edge.*Chromium.*supports/i);
  });

  it("renders pna-required panel with LNA label for Chrome 126+", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 Chrome/130.0.0.0 Safari/537.36",
    });
    render(
      <DiagnosticPanel
        compat={{ compatible: true, code: "pna-required", reason: "needs PNA" }}
        isHosted={true}
        hostUrl="http://localhost:8765"
      />
    );
    const panel = screen.getByTestId("diagnostic-panel");
    // Chrome 126+ uses "Local Network Access" label
    expect(panel).toHaveTextContent(/Local Network Access/i);
  });

  it("pna-required panel has external doc link", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 Chrome/122.0.0.0 Safari/537.36",
    });
    render(
      <DiagnosticPanel
        compat={{ compatible: true, code: "pna-required", reason: "needs PNA" }}
        isHosted={true}
        hostUrl="http://localhost:8765"
      />
    );
    const link = screen.getByRole("link", { name: /documentation/i });
    expect(link).toHaveAttribute("href");
    expect(link.getAttribute("href")).toContain("developer.chrome.com");
  });

  it("renders pna-required panel (safari — PNA not supported)", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Version/17.0 Safari/537.36",
    });
    render(
      <DiagnosticPanel
        compat={{ compatible: true, code: "pna-required", reason: "needs PNA" }}
        isHosted={true}
        hostUrl="http://localhost:8765"
      />
    );
    const panel = screen.getByTestId("diagnostic-panel");
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveTextContent(/Private Network Access/);
    // Safari path: shows HTTPS tunnel as workaround
    expect(panel).toHaveTextContent(/ngrok http 8765/);
    // Safari doc link points to webkit.org
    const link = screen.getByRole("link", { name: /documentation/i });
    expect(link.getAttribute("href")).toContain("webkit.org");
  });

  it("renders auth-required panel when probe found backend needing token", () => {
    render(
      <DiagnosticPanel
        compat={{ compatible: true, code: "ok", reason: null }}
        probeResult={{
          status: "auth-required",
          url: "http://127.0.0.1:8765",
          response: {
            schema: "browse-host/1",
            status: "ok",
            auth: "token",
            manual_token_required: true,
            capabilities: [],
            cors_origins_configured: true,
          },
        }}
        isHosted={true}
      />
    );
    const panel = screen.getByTestId("diagnostic-panel");
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveTextContent(/Backend requires authentication/);
    expect(panel).toHaveTextContent(/127\.0\.0\.1:8765/);
    expect(panel).toHaveTextContent(/Settings → Hosts/);
  });

  // ── Daemon state panels ───────────────────────────────────────────────────

  it("renders daemon-not-running panel when all candidates have daemonState=not-running", () => {
    render(
      <DiagnosticPanel
        compat={null}
        probeResult={{
          status: "unavailable",
          reasons: [
            { url: "http://127.0.0.1:8765", reason: "network-error", daemonState: "not-running" },
            { url: "http://localhost:8765", reason: "network-error", daemonState: "not-running" },
          ],
        }}
        isHosted={true}
      />
    );
    const panel = screen.getByTestId("diagnostic-panel");
    expect(panel).toHaveAttribute("aria-label", "Connectivity diagnostic: daemon not running");
    expect(panel).toHaveTextContent(/Local backend not running/);
    expect(panel).toHaveTextContent(/browse --hosted-bootstrap/);
  });

  it("renders daemon-no-pna panel when any candidate has daemonState=running-no-pna", () => {
    render(
      <DiagnosticPanel
        compat={null}
        probeResult={{
          status: "unavailable",
          reasons: [
            {
              url: "http://127.0.0.1:8765",
              reason: "network-error",
              daemonState: "running-no-pna",
            },
            {
              url: "http://localhost:8765",
              reason: "network-error",
              daemonState: "running-no-pna",
            },
          ],
        }}
        isHosted={true}
      />
    );
    const panel = screen.getByTestId("diagnostic-panel");
    expect(panel).toHaveAttribute(
      "aria-label",
      "Connectivity diagnostic: daemon running without --hosted-bootstrap"
    );
    expect(panel).toHaveTextContent(/Backend running/);
    expect(panel).toHaveTextContent(/--hosted-bootstrap/);
  });

  it("prefers running-no-pna over not-running when mixed daemon states", () => {
    render(
      <DiagnosticPanel
        compat={null}
        probeResult={{
          status: "unavailable",
          reasons: [
            {
              url: "http://127.0.0.1:8765",
              reason: "network-error",
              daemonState: "running-no-pna",
            },
            { url: "http://localhost:8765", reason: "network-error", daemonState: "not-running" },
          ],
        }}
        isHosted={true}
      />
    );
    const panel = screen.getByTestId("diagnostic-panel");
    // running-no-pna wins over not-running
    expect(panel).toHaveAttribute(
      "aria-label",
      "Connectivity diagnostic: daemon running without --hosted-bootstrap"
    );
  });

  it("shows PNA-blocked panel when daemon states are all unknown on hosted origin", () => {
    render(
      <DiagnosticPanel
        compat={null}
        probeResult={{
          status: "unavailable",
          reasons: [
            { url: "http://127.0.0.1:8765", reason: "network-error", daemonState: "unknown" },
          ],
        }}
        isHosted={true}
      />
    );
    const panel = screen.getByTestId("diagnostic-panel");
    expect(panel).toHaveTextContent(/Browser blocking local backend connection/);
    expect(panel).toHaveTextContent(/127\.0\.0\.1:8765/);
  });

  it("renders no-host-configured panel for hosted origin with no issues", () => {
    render(<DiagnosticPanel compat={null} isHosted={true} />);
    const panel = screen.getByTestId("diagnostic-panel");
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveTextContent(/No agent host configured/);
    expect(panel).toHaveTextContent(/Settings → Hosts/);
  });

  it("renders no-host-configured for ok compat on hosted origin", () => {
    render(
      <DiagnosticPanel compat={{ compatible: true, code: "ok", reason: null }} isHosted={true} />
    );
    expect(screen.getByTestId("diagnostic-panel")).toHaveTextContent(/No agent host configured/);
  });

  it("renders nothing for local origin with no issues", () => {
    const { container } = render(
      <DiagnosticPanel compat={{ compatible: true, code: "ok", reason: null }} isHosted={false} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for local origin even with ok compat and no probe result", () => {
    const { container } = render(
      <DiagnosticPanel compat={null} probeResult={null} isHosted={false} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  // ── Copy button interaction ───────────────────────────────────────────────

  it("copy button calls navigator.clipboard.writeText with the code text", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText },
    });

    render(
      <DiagnosticPanel
        compat={{ compatible: false, code: "mixed-content-http", reason: "test" }}
        isHosted={true}
        hostUrl="http://192.168.1.10:8765"
      />
    );

    // Click the first copy button (should copy the host URL)
    const copyButtons = screen.getAllByRole("button", { name: /copy/i });
    fireEvent.click(copyButtons[0]);

    // Clipboard write should have been called
    expect(writeText).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
