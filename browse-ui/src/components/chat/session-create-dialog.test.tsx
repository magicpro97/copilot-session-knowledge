import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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
}));

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
