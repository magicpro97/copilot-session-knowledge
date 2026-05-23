import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Mock } from "vitest";
import type { HostProfile } from "@/lib/api/types";
import { SessionCreateDialog } from "@/components/chat/session-create-dialog";
import { POPUP_SURFACE_BASE } from "@/components/ui/popup-surface";

const INITIAL_HOST: HostProfile = {
  id: "route-host",
  label: "Route Host",
  base_url: "https://route.ngrok.io",
  token: "tok-route",
  cli_kind: "copilot",
  is_default: false,
};

const OVERRIDE_HOST: HostProfile = {
  id: "override-host",
  label: "Override Host",
  base_url: "https://override.ngrok.io",
  token: "tok-override",
  cli_kind: "copilot",
  is_default: false,
};

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(() => "/chat"),
}));

vi.mock("@/lib/api/hooks", () => ({
  useOperatorModelCatalog: vi.fn(() => ({
    data: { models: [], default_model: "" },
    isLoading: false,
    isError: false,
  })),
  useCliSessions: vi.fn(() => ({
    data: { sessions: [], count: 0, truncated: false },
    isLoading: false,
    isError: false,
    error: null,
  })),
}));

// Default: cli_adopt is supported so existing tests continue to pass.
let cliAdoptSupportedMock = { supported: true, loading: false };

vi.mock("@/lib/hosts", () => ({
  useHostFeature: vi.fn(() => cliAdoptSupportedMock),
}));

import { useCliSessions } from "@/lib/api/hooks";
import { useHostFeature } from "@/lib/hosts";

vi.mock("./workspace-picker", () => ({
  WorkspacePicker: ({
    id,
    value,
    onChange,
  }: {
    id: string;
    value: string;
    onChange: (value: string) => void;
  }) => (
    <input
      id={id}
      aria-label="Workspace"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));

vi.mock("./host-picker", () => ({
  HostPicker: ({
    value,
    onChange,
  }: {
    value: HostProfile;
    onChange: (host: HostProfile) => void;
  }) => (
    <div>
      <div data-testid="host-picker-value">{value.id}</div>
      <button type="button" onClick={() => onChange(OVERRIDE_HOST)}>
        Override host
      </button>
    </div>
  ),
}));

vi.mock("@/providers/host-provider", () => ({
  useHostState: vi.fn(() => ({
    host: INITIAL_HOST,
    diagnosticsEnabled: true,
    localDiagnosticsEnabled: true,
  })),
}));

describe("SessionCreateDialog", () => {
  beforeEach(() => {
    cliAdoptSupportedMock = { supported: true, loading: false };
    vi.mocked(useCliSessions).mockReturnValue({
      data: { sessions: [], count: 0, truncated: false },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useCliSessions>);
  });

  it("preserves an in-progress host override while the dialog stays open", () => {
    const onSubmit = vi.fn();
    const { rerender } = render(
      <SessionCreateDialog onSubmit={onSubmit} initialHost={INITIAL_HOST} />
    );

    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    expect(screen.getByTestId("host-picker-value")).toHaveTextContent(INITIAL_HOST.id);

    fireEvent.click(screen.getByRole("button", { name: "Override host" }));
    expect(screen.getByTestId("host-picker-value")).toHaveTextContent(OVERRIDE_HOST.id);

    rerender(<SessionCreateDialog onSubmit={onSubmit} initialHost={{ ...INITIAL_HOST }} />);

    expect(screen.getByTestId("host-picker-value")).toHaveTextContent(OVERRIDE_HOST.id);
  });
});

// ── CLI adopt capability gate ─────────────────────────────────────────────────

describe("SessionCreateDialog — CLI tab capability gate", () => {
  beforeEach(() => {
    cliAdoptSupportedMock = { supported: true, loading: false };
    (useCliSessions as Mock).mockReturnValue({
      data: { sessions: [], count: 0, truncated: false },
      isLoading: false,
      isError: false,
      error: null,
    });
  });

  it("shows CLI tab when cli_adopt is supported and onAdopt is provided", () => {
    render(<SessionCreateDialog onSubmit={vi.fn()} onAdopt={vi.fn()} initialHost={INITIAL_HOST} />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    expect(screen.getByTestId("cli-history-tab")).toBeInTheDocument();
  });

  it("hides CLI tab when cli_adopt is NOT supported (remote modern backend)", () => {
    cliAdoptSupportedMock = { supported: false, loading: false };
    render(<SessionCreateDialog onSubmit={vi.fn()} onAdopt={vi.fn()} initialHost={INITIAL_HOST} />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    expect(screen.queryByTestId("cli-history-tab")).not.toBeInTheDocument();
  });

  it("hides CLI tab when capabilities are still loading", () => {
    cliAdoptSupportedMock = { supported: false, loading: true };
    render(<SessionCreateDialog onSubmit={vi.fn()} onAdopt={vi.fn()} initialHost={INITIAL_HOST} />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    // Tab is hidden while loading (issue #530: hide until supported)
    expect(screen.queryByTestId("cli-history-tab")).not.toBeInTheDocument();
  });

  it("hides CLI tab when onAdopt is not provided even if cli_adopt is supported", () => {
    render(<SessionCreateDialog onSubmit={vi.fn()} initialHost={INITIAL_HOST} />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    expect(screen.queryByTestId("cli-history-tab")).not.toBeInTheDocument();
  });

  it("does not fetch cli-sessions when cli_adopt is unsupported", () => {
    cliAdoptSupportedMock = { supported: false, loading: false };
    const mockUseCliSessions = vi.mocked(useCliSessions);
    mockUseCliSessions.mockClear();

    render(<SessionCreateDialog onSubmit={vi.fn()} onAdopt={vi.fn()} initialHost={INITIAL_HOST} />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    // useCliSessions should have been called with enabled=false
    const calls = mockUseCliSessions.mock.calls;
    expect(calls.length).toBeGreaterThan(0);
    const lastCall = calls.at(-1)!;
    // Second argument is the enabled flag — must be falsy
    expect(lastCall[1]).toBe(false);
  });

  it("calls useHostFeature with cli_adopt feature name", () => {
    const mockUseHostFeature = vi.mocked(useHostFeature);
    mockUseHostFeature.mockClear();

    render(<SessionCreateDialog onSubmit={vi.fn()} onAdopt={vi.fn()} initialHost={INITIAL_HOST} />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    const calls = mockUseHostFeature.mock.calls;
    const cliAdoptCall = calls.find((c) => c[1] === "cli_adopt");
    expect(cliAdoptCall).toBeDefined();
  });

  it("shows cliUnavailable state when useCliSessions returns a 404 error", () => {
    // After fix #4 (removing the 404→empty-success swallow), a 404 from the
    // backend now surfaces as isError=true so cliUnavailable becomes reachable.
    vi.mocked(useCliSessions).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("404 Not Found"),
    } as unknown as ReturnType<typeof useCliSessions>);

    render(<SessionCreateDialog onSubmit={vi.fn()} onAdopt={vi.fn()} initialHost={INITIAL_HOST} />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    fireEvent.click(screen.getByTestId("cli-history-tab"));

    expect(screen.getByText(/CLI history is not available on this host/i)).toBeInTheDocument();
  });
});

// ── Surface regression: session-create dialog uses shared popup surface ───────

describe("SessionCreateDialog — dialog surface contract", () => {
  it("session create dialog content carries all shared popup surface tokens", () => {
    render(<SessionCreateDialog onSubmit={vi.fn()} initialHost={INITIAL_HOST} />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    // DialogContent renders into a portal — findable via data-slot attribute
    const dialogContent = document.querySelector('[data-slot="dialog-content"]');
    expect(dialogContent).not.toBeNull();
    for (const token of POPUP_SURFACE_BASE.split(" ")) {
      expect(dialogContent!.className).toContain(token);
    }
  });

  it("session create dialog surface does not use semi-transparent background overrides", () => {
    render(<SessionCreateDialog onSubmit={vi.fn()} initialHost={INITIAL_HOST} />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    const dialogContent = document.querySelector('[data-slot="dialog-content"]');
    expect(dialogContent).not.toBeNull();
    const cls = dialogContent!.className;
    expect(cls).not.toMatch(/bg-popover\/\d+/);
    expect(cls).not.toContain("bg-transparent");
    expect(cls).not.toContain("backdrop-blur");
  });
});
