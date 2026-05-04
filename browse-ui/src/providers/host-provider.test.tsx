import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LOCAL_HOST } from "@/lib/host-profiles";
import { HostProvider, useHostState } from "@/providers/host-provider";

let pathnameMock = "/chat";

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(() => pathnameMock),
}));

function HostStateProbe() {
  const { host, diagnosticsEnabled } = useHostState();
  return (
    <>
      <div data-testid="host-id">{host.id}</div>
      <div data-testid="diagnostics-enabled">{String(diagnosticsEnabled)}</div>
    </>
  );
}

describe("HostProvider", () => {
  beforeEach(() => {
    pathnameMock = "/chat";
    localStorage.clear();
    window.history.pushState({}, "", "/chat");
  });

  it("recomputes diagnosticsEnabled when the pathname changes", async () => {
    const { rerender } = render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    expect(screen.getByTestId("host-id")).toHaveTextContent(LOCAL_HOST.id);
    expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("false");

    pathnameMock = "/v2/chat";
    window.history.pushState({}, "", "/v2/chat");
    rerender(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });
  });
});
