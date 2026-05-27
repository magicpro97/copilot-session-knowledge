import "@testing-library/jest-dom";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock buildHostUrl so tests do not make real network requests
vi.mock("@/lib/api/client", () => ({
  buildHostUrl: vi.fn((base: string, path: string) => new URL(path, base)),
}));

// Mock navigator.clipboard for copy-origin tests
Object.defineProperty(navigator, "clipboard", {
  value: { writeText: vi.fn().mockResolvedValue(undefined) },
  configurable: true,
});

// Mock local-bootstrap to avoid real fetch in detection tests
vi.mock("@/lib/hosts/local-bootstrap", () => ({
  probeLocalBootstrap: vi.fn(async () => ({ status: "unavailable" })),
  resetLocalBootstrapCache: vi.fn(),
}));
const { HostManagement } = await import("@/components/hosts/host-management");

// --- helpers ---

function renderHostManagement() {
  return render(<HostManagement />);
}

async function openAddForm() {
  fireEvent.click(screen.getByRole("button", { name: "Add remote host" }));
  await waitFor(() => expect(screen.getByTestId("host-add-form")).toBeInTheDocument());
}

// --- tests ---

describe("HostManagement — initial render", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders the local host row", () => {
    renderHostManagement();
    expect(screen.getByTestId("host-row-local")).toBeInTheDocument();
    expect(screen.getByText("Local (same-origin)")).toBeInTheDocument();
  });

  it("shows empty state when no remote hosts are saved", () => {
    renderHostManagement();
    expect(screen.getByTestId("no-remote-hosts")).toBeInTheDocument();
  });

  it("shows Add host button", () => {
    renderHostManagement();
    expect(screen.getByRole("button", { name: "Add remote host" })).toBeInTheDocument();
  });

  it("does not show hosted-origin strip on localhost", () => {
    // jsdom defaults to localhost
    renderHostManagement();
    expect(screen.queryByTestId("hosted-origin-strip")).not.toBeInTheDocument();
  });
});

describe("HostManagement — add host form", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("opens the add form when Add host is clicked", async () => {
    renderHostManagement();
    await openAddForm();
    expect(screen.getByLabelText("Tunnel URL")).toBeInTheDocument();
    expect(screen.getByLabelText("Host label")).toBeInTheDocument();
    expect(screen.getByLabelText("Auth token")).toBeInTheDocument();
  });

  it("disables Save host button when URL is empty", async () => {
    renderHostManagement();
    await openAddForm();
    expect(screen.getByTestId("save-host-btn")).toBeDisabled();
  });

  it("enables Save host button when URL is provided", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://abc123.ngrok.io" },
    });
    expect(screen.getByTestId("save-host-btn")).not.toBeDisabled();
  });

  it("closes the form and clears state when Cancel is clicked", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://abc123.ngrok.io" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByTestId("host-add-form")).not.toBeInTheDocument());
  });
});

describe("HostManagement — validation probe (issues #28 and #29)", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows validating state while probe is in flight", async () => {
    // fetch never resolves during this test
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}));

    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://abc123.ngrok.io" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByText("Validating…")).toBeInTheDocument());
    // Button disabled while in-flight
    expect(screen.getByTestId("save-host-btn")).toBeDisabled();
  });

  it("saves the host and closes the form when probe succeeds", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("{}", { status: 200 }));

    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://success.ngrok.io" },
    });
    fireEvent.change(screen.getByLabelText("Host label"), {
      target: { value: "My Server" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.queryByTestId("host-add-form")).not.toBeInTheDocument());
    expect(screen.getByText("My Server")).toBeInTheDocument();
    const [calledUrl, calledInit] = vi.mocked(fetch).mock.calls.at(-1) ?? [];
    expect(String(calledUrl)).toBe("https://success.ngrok.io/api/operator/capabilities");
    expect(calledInit).toMatchObject({
      method: "GET",
      headers: {},
    });
  });

  it("sends Authorization header when probing a protected remote host", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("{}", { status: 200 }));

    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://auth.ngrok.io" },
    });
    fireEvent.change(screen.getByLabelText("Auth token"), {
      target: { value: "secret-token" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.queryByTestId("host-add-form")).not.toBeInTheDocument());
    const [calledUrl, calledInit] = vi.mocked(fetch).mock.calls.at(-1) ?? [];
    expect(String(calledUrl)).toBe("https://auth.ngrok.io/api/operator/capabilities");
    expect(calledInit).toMatchObject({
      method: "GET",
      headers: { Authorization: "Bearer secret-token" },
    });
  });

  it("does not save the host when probe fails with a network error", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));

    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://unreachable.ngrok.io" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    // Host add form should still be visible — not saved
    expect(screen.getByTestId("host-add-form")).toBeInTheDocument();
    // The error message should mention CORS or reachability
    expect(screen.getByTestId("validation-error").textContent).toMatch(/CORS|reach|tunnel/i);
  });

  it("shows actionable 401 error when token is rejected", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("Unauthorized", { status: 401 }));

    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://test.ngrok.io" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    expect(screen.getByTestId("validation-error").textContent).toMatch(/401|token/i);
  });

  it("shows actionable 403 error mentioning CORS allowlist", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("Forbidden", { status: 403 }));

    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://test.ngrok.io" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    expect(screen.getByTestId("validation-error").textContent).toMatch(/403|CORS|allowlist/i);
  });

  it("shows a non-2xx HTTP status in the error message", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("Service Unavailable", { status: 503 }));

    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://test.ngrok.io" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    expect(screen.getByTestId("validation-error").textContent).toMatch(/503/);
  });

  it("offers 'Save anyway' escape hatch after a validation failure", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));

    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://private.ngrok.io" },
    });
    fireEvent.change(screen.getByLabelText("Host label"), {
      target: { value: "Skip Server" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("skip-validation-btn")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("skip-validation-btn"));

    await waitFor(() => expect(screen.queryByTestId("host-add-form")).not.toBeInTheDocument());
    expect(screen.getByText("Skip Server")).toBeInTheDocument();
  });
});

describe("HostManagement — hosted control-plane origin (issue #30)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("shows hosted-origin strip when origin is not localhost", () => {
    // Simulate a hosted deployment
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "https://agents.example.com" },
      configurable: true,
    });
    renderHostManagement();
    expect(screen.getByTestId("hosted-origin-strip")).toBeInTheDocument();
    expect(screen.getByText("https://agents.example.com")).toBeInTheDocument();

    // Reset
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "http://localhost:3000" },
      configurable: true,
    });
  });

  it("shows Copy button in the hosted-origin strip", () => {
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "https://agents.example.com" },
      configurable: true,
    });
    renderHostManagement();
    expect(screen.getByRole("button", { name: "Copy control-plane origin" })).toBeInTheDocument();

    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "http://localhost:3000" },
      configurable: true,
    });
  });

  it("calls clipboard.writeText with the origin when Copy is clicked", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "https://agents.example.com" },
      configurable: true,
    });

    renderHostManagement();
    fireEvent.click(screen.getByRole("button", { name: "Copy control-plane origin" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("https://agents.example.com"));

    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "http://localhost:3000" },
      configurable: true,
    });
  });

  it("treats 127.0.0.2 as a local origin instead of a hosted control plane", () => {
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "http://127.0.0.2:3000" },
      configurable: true,
    });

    renderHostManagement();
    expect(screen.queryByTestId("hosted-origin-strip")).not.toBeInTheDocument();
    expect(screen.getByText("Same-origin default")).toBeInTheDocument();

    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "http://localhost:3000" },
      configurable: true,
    });
  });
});

describe("HostManagement — loopback PNA note (formerly mixed-content guard)", () => {
  beforeEach(() => {
    localStorage.clear();
    // Simulate a hosted HTTPS deployment
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "https://agents.example.com" },
      configurable: true,
    });
    // Simulate probe returning a network error (loopback unreachable)
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("Failed to fetch");
      })
    );
  });

  afterEach(() => {
    // Restore to localhost so other test suites are unaffected
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "http://localhost:3000" },
      configurable: true,
    });
    vi.unstubAllGlobals();
  });

  it("shows a PNA informational note (not a hard error) for http://localhost from HTTPS origin", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "http://localhost:3000" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    // pna-note appears before probe completes (synchronous check sets it)
    await waitFor(() => expect(screen.getByTestId("pna-note")).toBeInTheDocument());
    // Probe will fail with network error and show validation-error
    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
  });

  it("shows 'Save anyway' after a probe failure for a loopback URL", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "http://127.0.0.1:8080" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    // Unlike a hard block, probe failures allow "Save anyway"
    expect(screen.getByTestId("skip-validation-btn")).toBeInTheDocument();
  });

  it("clears the PNA note when the URL field is updated", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "http://localhost:3000" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("pna-note")).toBeInTheDocument());

    // User corrects the URL to a valid HTTPS tunnel — pna-note should clear
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://mytunnel.ngrok.io" },
    });
    await waitFor(() => expect(screen.queryByTestId("pna-note")).not.toBeInTheDocument());
  });

  it("DOES fire a network probe for a loopback URL (pna-required is compatible: true)", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "http://localhost:3000" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    expect(vi.mocked(fetch)).toHaveBeenCalled();
  });

  it("does NOT fire a network probe for non-loopback HTTP from a hosted HTTPS origin", async () => {
    const fetchSpy = vi.mocked(fetch);
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "http://remote.example.com" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    expect(screen.getByTestId("validation-error").textContent).toMatch(/HTTPS|HTTP|loopback/i);
    expect(screen.queryByTestId("skip-validation-btn")).not.toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("HostManagement — URL scheme validation (issue #45)", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a hard error for a no-scheme URL (e.g. localhost without https://)", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "localhost" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    expect(screen.getByTestId("validation-error").textContent).toMatch(/valid URL|scheme/i);
  });

  it("shows a hard error for a javascript: URL", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "javascript:alert(1)" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    expect(screen.getByTestId("validation-error").textContent).toMatch(/scheme|not allowed/i);
  });

  it("does NOT show 'Save anyway' for a URL scheme error", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "localhost" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    expect(screen.queryByTestId("skip-validation-btn")).not.toBeInTheDocument();
  });

  it("does NOT fire a network probe for a URL with an invalid scheme", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "javascript:alert(1)" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("accepts https:// URL and proceeds to probe", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("{}", { status: 200 }));

    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://valid.ngrok.io" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.queryByTestId("host-add-form")).not.toBeInTheDocument());
    expect(vi.mocked(fetch)).toHaveBeenCalled();
  });
});

// ── Detect local backend affordance (issue #49) ──────────────────────────────

describe("HostManagement — detect local backend affordance", () => {
  beforeEach(async () => {
    localStorage.clear();
    // Hosted origin so the detect button appears
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "https://agents.example.com" },
      configurable: true,
    });
    const { probeLocalBootstrap, resetLocalBootstrapCache } =
      await import("@/lib/hosts/local-bootstrap");
    vi.mocked(probeLocalBootstrap).mockResolvedValue({ status: "unavailable" });
    vi.mocked(resetLocalBootstrapCache).mockReset();
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "http://localhost:3000" },
      configurable: true,
    });
  });

  it("renders the Detect local backend button on a hosted origin", () => {
    renderHostManagement();
    expect(screen.getByTestId("detect-local-btn")).toBeInTheDocument();
  });

  it("does NOT render the Detect local backend button on localhost", () => {
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "http://localhost:3000" },
      configurable: true,
    });
    renderHostManagement();
    expect(screen.queryByTestId("detect-local-btn")).not.toBeInTheDocument();
  });

  it("shows unavailable message when probe returns unavailable", async () => {
    renderHostManagement();
    fireEvent.click(screen.getByTestId("detect-local-btn"));

    await waitFor(() =>
      expect(screen.getByTestId("detect-result-unavailable")).toBeInTheDocument()
    );
    expect(screen.getByTestId("detect-result-unavailable").textContent).toMatch(
      /127.0.0.1:8765|localhost:8765/
    );
    const openLocal = screen.getByTestId("detect-result-open-local-ui");
    expect(openLocal).toHaveAttribute("href", "http://127.0.0.1:8765/");
    expect(openLocal).toHaveAttribute("target", "_blank");
  });

  it("shows detected message and add-button when probe detects a backend", async () => {
    const { probeLocalBootstrap } = await import("@/lib/hosts/local-bootstrap");
    vi.mocked(probeLocalBootstrap).mockResolvedValueOnce({
      status: "detected",
      url: "http://127.0.0.1:8765",
      response: {
        schema: "browse-host/1",
        status: "ok",
        auth: "open",
        manual_token_required: false,
        capabilities: ["chat"],
        cors_origins_configured: true,
      },
    });

    renderHostManagement();
    fireEvent.click(screen.getByTestId("detect-local-btn"));

    await waitFor(() => expect(screen.getByTestId("detect-result-detected")).toBeInTheDocument());
    expect(screen.getByTestId("detect-result-detected").textContent).toMatch(/127.0.0.1:8765/);
    expect(screen.getByTestId("add-detected-btn")).toBeInTheDocument();
  });

  it("shows auth-required message and pre-fills form when probe requires token", async () => {
    const { probeLocalBootstrap } = await import("@/lib/hosts/local-bootstrap");
    vi.mocked(probeLocalBootstrap).mockResolvedValueOnce({
      status: "auth-required",
      url: "http://127.0.0.1:8765",
      response: {
        schema: "browse-host/1",
        status: "ok",
        auth: "token",
        manual_token_required: true,
        capabilities: ["chat"],
        cors_origins_configured: true,
      },
    });

    renderHostManagement();
    fireEvent.click(screen.getByTestId("detect-local-btn"));

    await waitFor(() =>
      expect(screen.getByTestId("detect-result-auth-required")).toBeInTheDocument()
    );
    // Form should open and URL pre-filled
    await waitFor(() => expect(screen.getByTestId("host-add-form")).toBeInTheDocument());
    expect(screen.getByLabelText("Tunnel URL")).toHaveValue("http://127.0.0.1:8765");
  });

  it("calls resetLocalBootstrapCache before probing", async () => {
    const { resetLocalBootstrapCache } = await import("@/lib/hosts/local-bootstrap");

    renderHostManagement();
    fireEvent.click(screen.getByTestId("detect-local-btn"));

    await waitFor(() =>
      expect(screen.getByTestId("detect-result-unavailable")).toBeInTheDocument()
    );
    expect(resetLocalBootstrapCache).toHaveBeenCalled();
  });
});

describe("HostManagement — telemetry toggle and consent (#558)", () => {
  const REMOTE_ID = "remote-acme";
  const PROFILES_STORAGE_KEY = "browse_host_profiles";

  /** Seed a remote host into storage so the telemetry toggle is rendered. */
  function seedRemoteHost(overrides: Record<string, unknown> = {}): void {
    const profile = {
      id: REMOTE_ID,
      label: "Acme remote",
      base_url: "https://acme.example.com",
      token: "",
      cli_kind: "copilot",
      is_default: false,
      ...overrides,
    };
    localStorage.setItem(PROFILES_STORAGE_KEY, JSON.stringify([profile]));
  }

  /** Read the stored remote host back from localStorage. */
  function readRemoteHost(): Record<string, unknown> | null {
    const raw = localStorage.getItem(PROFILES_STORAGE_KEY);
    if (!raw) return null;
    const arr = JSON.parse(raw) as Array<Record<string, unknown>>;
    return arr.find((p) => p.id === REMOTE_ID) ?? null;
  }

  beforeEach(() => {
    localStorage.clear();
  });

  it("shows the consent banner on first opt-in and does not flip telemetry_enabled yet", () => {
    seedRemoteHost();
    renderHostManagement();

    expect(screen.queryByTestId("telemetry-consent-banner")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId(`host-telemetry-toggle-${REMOTE_ID}`));

    expect(screen.getByTestId("telemetry-consent-banner")).toBeInTheDocument();
    // Storage must NOT yet reflect telemetry_enabled=true — consent gates the flip.
    const stored = readRemoteHost();
    expect(stored?.telemetry_enabled).not.toBe(true);
  });

  it("enables telemetry and marks consent acked when the operator confirms", async () => {
    seedRemoteHost();
    renderHostManagement();

    fireEvent.click(screen.getByTestId(`host-telemetry-toggle-${REMOTE_ID}`));
    fireEvent.click(screen.getByTestId("telemetry-consent-confirm"));

    await waitFor(() =>
      expect(screen.queryByTestId("telemetry-consent-banner")).not.toBeInTheDocument()
    );
    const stored = readRemoteHost();
    expect(stored?.telemetry_enabled).toBe(true);
    expect(stored?.telemetry_consent_acked).toBe(true);
  });

  it("dismisses the banner without enabling telemetry when the operator cancels", () => {
    seedRemoteHost();
    renderHostManagement();

    fireEvent.click(screen.getByTestId(`host-telemetry-toggle-${REMOTE_ID}`));
    fireEvent.click(screen.getByTestId("telemetry-consent-cancel"));

    expect(screen.queryByTestId("telemetry-consent-banner")).not.toBeInTheDocument();
    const stored = readRemoteHost();
    expect(stored?.telemetry_enabled).not.toBe(true);
    // Consent must NOT be persisted when the operator declines.
    expect(stored?.telemetry_consent_acked).not.toBe(true);
  });

  it("re-enables telemetry without re-prompting when consent was previously acked", () => {
    // Operator already acked consent in a prior session and later toggled off.
    seedRemoteHost({ telemetry_enabled: false, telemetry_consent_acked: true });
    renderHostManagement();

    fireEvent.click(screen.getByTestId(`host-telemetry-toggle-${REMOTE_ID}`));

    expect(screen.queryByTestId("telemetry-consent-banner")).not.toBeInTheDocument();
    const stored = readRemoteHost();
    expect(stored?.telemetry_enabled).toBe(true);
    expect(stored?.telemetry_consent_acked).toBe(true);
  });

  it("disables telemetry immediately without showing the consent banner", () => {
    seedRemoteHost({ telemetry_enabled: true, telemetry_consent_acked: true });
    renderHostManagement();

    fireEvent.click(screen.getByTestId(`host-telemetry-toggle-${REMOTE_ID}`));

    expect(screen.queryByTestId("telemetry-consent-banner")).not.toBeInTheDocument();
    const stored = readRemoteHost();
    expect(stored?.telemetry_enabled).toBe(false);
    // Consent ack is preserved across toggle-off so subsequent opt-in skips the banner.
    expect(stored?.telemetry_consent_acked).toBe(true);
  });
});
