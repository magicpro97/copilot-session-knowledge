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

// Dynamic import avoids "use client" directive issues in the test environment
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

describe("HostManagement — mixed-content loopback guard", () => {
  beforeEach(() => {
    localStorage.clear();
    // Simulate a hosted HTTPS deployment
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "https://agents.example.com" },
      configurable: true,
    });
  });

  afterEach(() => {
    // Restore to localhost so other test suites are unaffected
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "http://localhost:3000" },
      configurable: true,
    });
  });

  it("shows a hard error when entering an http://localhost URL from a hosted HTTPS origin", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "http://localhost:3000" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    expect(screen.getByTestId("validation-error").textContent).toMatch(/HTTPS|loopback|tunnel/i);
  });

  it("does NOT show 'Save anyway' for a mixed-content loopback error", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "http://127.0.0.1:8080" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    expect(screen.queryByTestId("skip-validation-btn")).not.toBeInTheDocument();
  });

  it("clears the hard error when the URL field is updated", async () => {
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "http://localhost:3000" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());

    // User corrects the URL to a valid HTTPS tunnel
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://mytunnel.ngrok.io" },
    });
    await waitFor(() => expect(screen.queryByTestId("validation-error")).not.toBeInTheDocument());
  });

  it("does NOT fire a network probe for a mixed-content incompatible URL", async () => {
    vi.stubGlobal("fetch", vi.fn());
    renderHostManagement();
    await openAddForm();
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "http://localhost:3000" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));

    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
