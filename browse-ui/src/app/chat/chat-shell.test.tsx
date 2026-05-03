import { render, screen, fireEvent } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

beforeAll(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

vi.mock("next/navigation", () => ({
  useRouter: vi.fn(() => ({ push: vi.fn() })),
  useSearchParams: vi.fn(() => new URLSearchParams()),
  usePathname: vi.fn(() => "/chat"),
}));

vi.mock("@/lib/api/hooks", () => ({
  useOperatorSessions: vi.fn(() => ({
    data: { sessions: [], count: 0 },
    isLoading: false,
    isError: false,
  })),
  useOperatorSession: vi.fn(() => ({
    data: null,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  })),
  useOperatorRuns: vi.fn(() => ({
    data: { runs: [], count: 0 },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  })),
  useCreateOperatorSession: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useDeleteOperatorSession: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useSubmitPrompt: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePathSuggest: vi.fn(() => ({ data: { suggestions: [], count: 0 } })),
  useOperatorModelCatalog: vi.fn(() => ({
    data: {
      models: [
        { id: "gpt-5.4", display_name: "GPT 5.4", provider: "OpenAI", default: true },
        { id: "claude-sonnet-4.6", display_name: "Claude Sonnet 4.6", provider: "Anthropic" },
      ],
      default_model: "gpt-5.4",
    },
    isLoading: false,
    isError: false,
  })),
  useFilePreview: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useFileDiff: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  createOperatorStreamPath: vi.fn(() => "/api/operator/sessions/x/stream?run=y"),
}));

import { ChatShell } from "@/components/chat/chat-shell";
import { COPILOT_MODES } from "@/components/chat/session-create-dialog";

describe("ChatShell", () => {
  it("renders the chat shell container", () => {
    render(<ChatShell />);
    expect(screen.getByTestId("chat-shell")).toBeInTheDocument();
  });

  it("shows empty state when no session is selected", () => {
    render(<ChatShell />);
    expect(screen.getByText("No session selected")).toBeInTheDocument();
  });

  it("shows 'Chat Sessions' heading in the sidebar", () => {
    render(<ChatShell />);
    expect(screen.getByText("Chat Sessions")).toBeInTheDocument();
  });

  it("shows 'New Chat' button", () => {
    render(<ChatShell />);
    expect(screen.getByRole("button", { name: "New chat session" })).toBeInTheDocument();
  });

  it("shows mobile 'Open session list' button", () => {
    render(<ChatShell />);
    expect(screen.getByRole("button", { name: "Open session list" })).toBeInTheDocument();
  });
});

describe("ChatShell — multi-run history", () => {
  it("renders multiple historical runs from useOperatorRuns", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    vi.mocked(hooks.useOperatorRuns).mockReturnValue({
      data: {
        runs: [
          {
            id: "run-1",
            session_id: "session-1",
            prompt: "First prompt",
            status: "done",
            exit_code: 0,
            started_at: "2024-01-01T00:00:00Z",
            finished_at: "2024-01-01T00:01:00Z",
            events: [],
          },
          {
            id: "run-2",
            session_id: "session-1",
            prompt: "Second prompt",
            status: "done",
            exit_code: 0,
            started_at: "2024-01-01T00:02:00Z",
            finished_at: "2024-01-01T00:03:00Z",
            events: [],
          },
        ],
        count: 2,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorRuns>);

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: {
        id: "session-1",
        name: "My Session",
        model: "claude-sonnet-4.5",
        mode: "interactive",
        workspace: "/Users/user/projects/app",
        add_dirs: [],
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:03:00Z",
        run_count: 2,
        last_run_id: "run-2",
        resume_ready: true,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("s=session-1") as ReturnType<typeof navigation.useSearchParams>
    );

    render(<ChatShell />);
    expect(screen.getByText("First prompt")).toBeInTheDocument();
    expect(screen.getByText("Second prompt")).toBeInTheDocument();
  });
});

describe("COPILOT_MODES — valid CLI mode values", () => {
  const VALID_MODES = new Set(["interactive", "plan", "autopilot"]);
  const INVALID_MODES = new Set(["default", "code", "edit", "agent"]);

  it("only contains valid Copilot CLI 1.0.40 mode values", () => {
    for (const mode of COPILOT_MODES) {
      expect(VALID_MODES.has(mode.value), `"${mode.value}" is not a valid CLI mode`).toBe(true);
    }
  });

  it("does not contain any invalid/removed mode values", () => {
    for (const mode of COPILOT_MODES) {
      expect(INVALID_MODES.has(mode.value), `"${mode.value}" is an invalid CLI mode`).toBe(false);
    }
  });

  it("includes all three required modes", () => {
    const values = COPILOT_MODES.map((m) => m.value);
    expect(values).toContain("interactive");
    expect(values).toContain("plan");
    expect(values).toContain("autopilot");
  });
});

describe("SessionCreateDialog — dynamic model input", () => {
  it("renders a text input for model selection (not a locked dropdown)", async () => {
    render(<ChatShell />);

    // Open the dialog
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    // The model field should be a text input, not a select
    const modelInput = screen.getByLabelText("Model");
    expect(modelInput.tagName).toBe("INPUT");
    expect((modelInput as HTMLInputElement).type).toBe("text");
  });

  it("model input has a datalist sourced from the operator model catalog", async () => {
    render(<ChatShell />);

    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    const modelInput = screen.getByLabelText("Model") as HTMLInputElement;
    const listId = modelInput.getAttribute("list");
    expect(listId).toBeTruthy();

    const datalist = document.getElementById(listId!);
    expect(datalist).not.toBeNull();
    expect(datalist!.tagName).toBe("DATALIST");

    const options = datalist!.querySelectorAll("option");
    const values = Array.from(options).map((o) => o.getAttribute("value"));
    expect(values).toContain("gpt-5.4");
    expect(values).toContain("claude-sonnet-4.6");
  });

  it("model input accepts arbitrary freetext model identifiers", async () => {
    render(<ChatShell />);

    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    const modelInput = screen.getByLabelText("Model") as HTMLInputElement;
    fireEvent.change(modelInput, { target: { value: "my-custom-model-v1" } });
    expect(modelInput.value).toBe("my-custom-model-v1");
  });
});

describe("WorkspacePicker — hidden-folder toggle", () => {
  it("renders a toggle button for showing hidden folders", () => {
    render(<ChatShell />);

    // Open the dialog to expose WorkspacePicker
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    const toggleBtn = screen.getByRole("button", { name: "Show hidden folders" });
    expect(toggleBtn).toBeInTheDocument();
    expect(toggleBtn).toHaveAttribute("aria-pressed", "false");
  });

  it("toggle button switches to 'Hide hidden folders' when activated", () => {
    render(<ChatShell />);

    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    const toggleBtn = screen.getByRole("button", { name: "Show hidden folders" });
    fireEvent.click(toggleBtn);

    expect(screen.getByRole("button", { name: "Hide hidden folders" })).toBeInTheDocument();
  });
});
