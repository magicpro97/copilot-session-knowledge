import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { HostProfile } from "@/lib/api/types";
import { SessionCreateDialog } from "@/components/chat/session-create-dialog";

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
