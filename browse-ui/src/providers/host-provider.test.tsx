import "@testing-library/jest-dom";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LOCAL_HOST, saveHostProfile, setSelectedHostId } from "@/lib/host-profiles";
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

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("enables diagnostics for LOCAL_HOST when same-origin health probe succeeds", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
      }))
    );

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    expect(screen.getByTestId("host-id")).toHaveTextContent(LOCAL_HOST.id);
    await waitFor(() => {
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });
  });

  it("keeps diagnostics disabled for LOCAL_HOST when same-origin health probe fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
      }))
    );

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    expect(screen.getByTestId("host-id")).toHaveTextContent(LOCAL_HOST.id);
    await waitFor(() => {
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("false");
    });
  });

  it("does not overwrite a later remote-host selection when the local health probe resolves", async () => {
    let resolveFetch: ((value: { ok: boolean }) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<{ ok: boolean }>((resolve) => {
            resolveFetch = resolve;
          })
      )
    );

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    expect(screen.getByTestId("host-id")).toHaveTextContent(LOCAL_HOST.id);

    act(() => {
      saveHostProfile({
        id: "remote-a",
        label: "Remote A",
        base_url: "https://agent.example.com",
        token: "secret",
        cli_kind: "copilot",
        is_default: false,
      });
      setSelectedHostId("remote-a");
    });

    await waitFor(() => {
      expect(screen.getByTestId("host-id")).toHaveTextContent("remote-a");
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });

    await act(async () => {
      resolveFetch?.({ ok: true });
    });

    await waitFor(() => {
      expect(screen.getByTestId("host-id")).toHaveTextContent("remote-a");
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });
  });
});
