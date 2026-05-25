import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { HostProfile } from "@/lib/api/types";

const { mockHostState, mockIsLocalOrigin, mockLocalHost, mockRouterPush, mockSetTheme } =
  vi.hoisted(() => {
    const localHost = {
      id: "local",
      label: "Local",
      base_url: "",
      token: "",
      cli_kind: "copilot",
      is_default: true,
    };

    return {
      mockHostState: { current: { host: localHost, diagnosticsEnabled: true } },
      mockIsLocalOrigin: vi.fn(() => true),
      mockLocalHost: localHost,
      mockRouterPush: vi.fn(),
      mockSetTheme: vi.fn(),
    };
  });

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockRouterPush }),
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "dark", setTheme: mockSetTheme }),
}));

vi.mock("@/hooks/use-resolved-pathname", () => ({
  useResolvedPathname: () => "/sessions",
}));

vi.mock("@/hooks/use-keyboard-platform", () => ({
  useKeyboardPlatform: () => "mac",
}));

vi.mock("@/providers/host-provider", () => ({
  useHostState: () => mockHostState.current,
}));

vi.mock("@/lib/host-profiles", () => ({
  BROWSE_HOST_CHANGE_EVENT: "browse-host-change",
  LOCAL_HOST: mockLocalHost,
  LOCAL_HOST_ID: "local",
  getAllHostProfiles: () => [mockLocalHost],
  isLocalOrigin: mockIsLocalOrigin,
  setSelectedHostId: vi.fn(),
}));

const { Header } = await import("./header");

function makeRemoteHost(baseUrl: string): HostProfile {
  return {
    id: `remote-${baseUrl}`,
    label: "Remote host",
    base_url: baseUrl,
    token: "",
    cli_kind: "copilot",
    is_default: false,
  };
}

function setActiveHost(host: HostProfile) {
  mockHostState.current = { host, diagnosticsEnabled: true };
}

describe("Header hosted backend guide", () => {
  beforeEach(() => {
    setActiveHost(mockLocalHost as HostProfile);
    mockIsLocalOrigin.mockReset();
    mockIsLocalOrigin.mockReturnValue(true);
    mockRouterPush.mockReset();
    mockSetTheme.mockReset();
  });

  it("should show backend startup guidance on hosted origins with the local host selected", async () => {
    mockIsLocalOrigin.mockReturnValue(false);

    render(<Header />);

    const banner = await screen.findByTestId("hosted-backend-guide-banner");
    expect(banner).toHaveTextContent(/browse --hosted-bootstrap --open-browser chrome/);
    expect(screen.getByTestId("hosted-backend-guide-open-https")).toHaveAttribute(
      "href",
      "https://127.0.0.1:8765/"
    );
    expect(screen.getByTestId("hosted-backend-guide-open-http")).toHaveAttribute(
      "href",
      "http://127.0.0.1:8765/"
    );
  });

  it("should show guidance when a hosted origin selects a loopback host", async () => {
    mockIsLocalOrigin.mockReturnValue(false);
    setActiveHost(makeRemoteHost("https://127.0.0.1:8765"));

    render(<Header />);

    expect(await screen.findByTestId("hosted-backend-guide-banner")).toBeInTheDocument();
  });

  it("should hide guidance on local origins", async () => {
    render(<Header />);

    await waitFor(() => expect(mockIsLocalOrigin).toHaveBeenCalled());
    expect(screen.queryByTestId("hosted-backend-guide-banner")).not.toBeInTheDocument();
  });

  it("should hide guidance for non-loopback remote hosts on hosted origins", async () => {
    mockIsLocalOrigin.mockReturnValue(false);
    setActiveHost(makeRemoteHost("https://agent.example.test"));

    render(<Header />);

    await waitFor(() => expect(mockIsLocalOrigin).toHaveBeenCalled());
    expect(screen.queryByTestId("hosted-backend-guide-banner")).not.toBeInTheDocument();
  });
});
