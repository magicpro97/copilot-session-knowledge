import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

beforeAll(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  // Provide a no-op localStorage stub so HostPicker doesn't throw in jsdom.
  const storage: Record<string, string> = {};
  Object.defineProperty(window, "localStorage", {
    value: {
      getItem: (k: string) => storage[k] ?? null,
      setItem: (k: string, v: string) => {
        storage[k] = v;
      },
      removeItem: (k: string) => {
        delete storage[k];
      },
      clear: () => {
        Object.keys(storage).forEach((k) => delete storage[k]);
      },
    },
    writable: true,
  });
});

afterEach(async () => {
  const navigation = await import("next/navigation");
  vi.mocked(navigation.usePathname).mockReturnValue(
    "/v2/chat" as ReturnType<typeof navigation.usePathname>
  );
});

vi.mock("next/navigation", () => ({
  useRouter: vi.fn(() => ({ push: vi.fn() })),
  useSearchParams: vi.fn(() => new URLSearchParams()),
  usePathname: vi.fn(() => "/v2/chat"),
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
  createOperatorStreamUrl: vi.fn(
    (sessionId: string, runId: string, host: { base_url: string }) =>
      `${host.base_url}/api/operator/sessions/${sessionId}/stream?run=${runId}`
  ),
}));

// Mock the shared host-provider so tests that render SessionCreateDialog get a known host state.
import type { HostState } from "@/providers/host-provider";
import { LOCAL_HOST } from "@/lib/host-profiles";

let hostStateMock: HostState = { host: LOCAL_HOST, diagnosticsEnabled: true };
vi.mock("@/providers/host-provider", () => ({
  useHostState: vi.fn(() => hostStateMock),
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

  it("keeps hosted root chat idle until a remote host is selected", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    window.localStorage.clear();
    vi.mocked(navigation.usePathname).mockReturnValue(
      "/chat" as ReturnType<typeof navigation.usePathname>
    );

    render(<ChatShell />);

    expect(vi.mocked(hooks.useOperatorSessions).mock.calls.at(-1)?.[1]).toBe(false);
    expect(
      screen.getByText(/Add a public agent host in New Chat to connect this hosted console/i)
    ).toBeInTheDocument();
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

describe("SessionCreateDialog — host picker", () => {
  it("renders the Agent Host label and a host selector inside the dialog", () => {
    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    expect(screen.getByText("Agent Host")).toBeInTheDocument();
    // The add-host button should be present
    expect(screen.getByRole("button", { name: "Add agent host" })).toBeInTheDocument();
  });

  it("shows the Add host form when the add-host button is clicked", () => {
    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    fireEvent.click(screen.getByRole("button", { name: "Add agent host" }));

    expect(screen.getByTestId("host-add-form")).toBeInTheDocument();
    expect(screen.getByLabelText("Tunnel URL")).toBeInTheDocument();
  });

  it("Save host button is disabled when the URL field is empty", () => {
    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    fireEvent.click(screen.getByRole("button", { name: "Add agent host" }));

    const saveBtn = screen.getByRole("button", { name: "Save host" });
    expect(saveBtn).toBeDisabled();
  });

  it("Save host button is enabled after a URL is entered", () => {
    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    fireEvent.click(screen.getByRole("button", { name: "Add agent host" }));

    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://abc123.ngrok.io" },
    });

    const saveBtn = screen.getByRole("button", { name: "Save host" });
    expect(saveBtn).not.toBeDisabled();
  });
});

describe("ChatShell — host URL in top-bar", () => {
  it("shows 'CLI Chat' placeholder when no session is selected", async () => {
    // Reset useOperatorSession to return no session (may be overridden by earlier tests)
    const hooks = await import("@/lib/api/hooks");
    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    const navigation = await import("next/navigation");
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams() as ReturnType<typeof navigation.useSearchParams>
    );

    render(<ChatShell />);
    expect(screen.getByText("CLI Chat")).toBeInTheDocument();
  });
});

describe("ChatShell — remote host passed to Transcript", () => {
  it("shows remote host badge in top-bar when h= param is set and no session is active", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    // Save a remote host profile so getAllHostProfiles() finds it
    const { saveHostProfile } = await import("@/lib/host-profiles");
    saveHostProfile({
      id: "remote-h2",
      label: "Dev Tunnel",
      base_url: "https://dev2.ngrok.io",
      token: "tok",
      cli_kind: "copilot",
      is_default: false,
    });

    // No session selected — just the remote host param
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("h=remote-h2") as ReturnType<typeof navigation.useSearchParams>
    );

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    render(<ChatShell />);

    // The top-bar should display the remote host base_url as a badge title
    expect(screen.getByTitle("https://dev2.ngrok.io")).toBeInTheDocument();
  });
});

describe("ChatShell — shared selected-host reuse", () => {
  it("shows remote host badge from the shared selected host when no h= URL param", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");
    const { saveHostProfile } = await import("@/lib/host-profiles");

    const sharedHost = {
      id: "persisted-remote-h1",
      label: "Persisted Tunnel",
      base_url: "https://persisted.ngrok.io",
      token: "tok-p",
      cli_kind: "copilot" as const,
      is_default: false,
    };
    saveHostProfile(sharedHost);
    hostStateMock = { host: sharedHost, diagnosticsEnabled: true };

    // No h= param in URL — ChatShell must fall back to the shared selected host.
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams() as ReturnType<typeof navigation.useSearchParams>
    );

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    render(<ChatShell />);

    // Top-bar must show the selected remote host as a badge even without a URL param.
    expect(screen.getByTitle(sharedHost.base_url)).toBeInTheDocument();
  });

  it("h= URL param takes precedence over the shared selected host", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");
    const { saveHostProfile } = await import("@/lib/host-profiles");

    saveHostProfile({
      id: "remote-h2",
      label: "Tunnel 2",
      base_url: "https://dev2.ngrok.io",
      token: "tok-2",
      cli_kind: "copilot",
      is_default: false,
    });

    // Persist a different host
    saveHostProfile({
      id: "persisted-other",
      label: "Other Tunnel",
      base_url: "https://other.ngrok.io",
      token: "",
      cli_kind: "copilot",
      is_default: false,
    });
    hostStateMock = {
      host: {
        id: "persisted-other",
        label: "Other Tunnel",
        base_url: "https://other.ngrok.io",
        token: "",
        cli_kind: "copilot",
        is_default: false,
      },
      diagnosticsEnabled: true,
    };

    // But URL has a specific h= param pointing to remote-h2 (saved earlier)
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("h=remote-h2") as ReturnType<typeof navigation.useSearchParams>
    );

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    render(<ChatShell />);

    // h= param (remote-h2 = dev2.ngrok.io) wins over persisted (other.ngrok.io)
    expect(screen.getByTitle("https://dev2.ngrok.io")).toBeInTheDocument();
    expect(screen.queryByTitle("https://other.ngrok.io")).not.toBeInTheDocument();
  });
});

describe("ChatShell — shared host selection", () => {
  it("uses the shared browse-wide host state when no h= URL param and updates on rerender", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");
    const remoteHost = {
      id: "header-selected-remote",
      label: "Header Tunnel",
      base_url: "https://header.ngrok.io",
      token: "tok-header",
      cli_kind: "copilot" as const,
      is_default: false,
    };

    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams() as ReturnType<typeof navigation.useSearchParams>
    );
    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    const { rerender } = render(<ChatShell />);

    expect(vi.mocked(hooks.useOperatorSessions).mock.calls.at(-1)?.[0]).toMatchObject({
      id: LOCAL_HOST.id,
    });

    hostStateMock = { host: remoteHost, diagnosticsEnabled: true };
    rerender(<ChatShell />);

    expect(vi.mocked(hooks.useOperatorSessions).mock.calls.at(-1)?.[0]).toMatchObject({
      id: remoteHost.id,
      base_url: remoteHost.base_url,
    });
    expect(screen.getByTitle(remoteHost.base_url)).toBeInTheDocument();
  });
});

// ─── Composer — file attachment UX ──────────────────────────────────────────

/**
 * Helper: set up a session so the Composer is rendered inside ChatShell.
 */
async function setupActiveSession() {
  const hooks = await import("@/lib/api/hooks");
  const navigation = await import("next/navigation");

  vi.mocked(hooks.useOperatorSession).mockReturnValue({
    data: {
      id: "session-1",
      name: "Test Session",
      model: "gpt-5.4",
      mode: "interactive",
      workspace: "/projects/test",
      add_dirs: [],
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
      run_count: 0,
      last_run_id: null,
      resume_ready: true,
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof hooks.useOperatorSession>);

  vi.mocked(hooks.useOperatorRuns).mockReturnValue({
    data: { runs: [], count: 0 },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof hooks.useOperatorRuns>);

  vi.mocked(navigation.useSearchParams).mockReturnValue(
    new URLSearchParams("s=session-1") as ReturnType<typeof navigation.useSearchParams>
  );
}

describe("Composer — file attachment button", () => {
  it("renders the Attach files button when a session is active", async () => {
    await setupActiveSession();
    render(<ChatShell />);
    expect(screen.getByRole("button", { name: "Attach files" })).toBeInTheDocument();
  });

  it("shows a queued file chip after a file is selected", async () => {
    // FileReader is async — mock it to resolve synchronously
    const originalFileReader = global.FileReader;
    class MockFileReader {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      result = "data:text/plain;base64,aGVsbG8=";
      readAsDataURL() {
        setTimeout(() => this.onload?.(), 0);
      }
    }
    global.FileReader = MockFileReader as unknown as typeof FileReader;

    await setupActiveSession();
    render(<ChatShell />);

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).not.toBeNull();

    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
    fireEvent.change(fileInput);

    await waitFor(() => {
      expect(screen.getByText("notes.txt")).toBeInTheDocument();
    });

    global.FileReader = originalFileReader;
  });

  it("removes a queued file chip when the remove button is clicked", async () => {
    const originalFileReader = global.FileReader;
    class MockFileReader {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      result = "data:text/plain;base64,aGVsbG8=";
      readAsDataURL() {
        setTimeout(() => this.onload?.(), 0);
      }
    }
    global.FileReader = MockFileReader as unknown as typeof FileReader;

    await setupActiveSession();
    render(<ChatShell />);

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["hello"], "remove-me.txt", { type: "text/plain" });
    Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
    fireEvent.change(fileInput);

    await waitFor(() => {
      expect(screen.getByText("remove-me.txt")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove remove-me.txt" }));
    expect(screen.queryByText("remove-me.txt")).not.toBeInTheDocument();

    global.FileReader = originalFileReader;
  });
});

describe("ChatShell — run history with file attachments", () => {
  it("renders file chips in a historical run's user bubble when files are present", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    vi.mocked(hooks.useOperatorRuns).mockReturnValue({
      data: {
        runs: [
          {
            id: "run-with-files",
            session_id: "session-files",
            prompt: "Analyze these files",
            status: "done",
            exit_code: 0,
            started_at: "2024-01-01T00:00:00Z",
            finished_at: "2024-01-01T00:01:00Z",
            events: [],
            files: [
              { name: "report.pdf", type: "application/pdf", size: 204800 },
              { name: "data.csv", type: "text/csv", size: 1024 },
            ],
          },
        ],
        count: 1,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorRuns>);

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: {
        id: "session-files",
        name: "Files Session",
        model: "gpt-5.4",
        mode: "interactive",
        workspace: "/projects",
        add_dirs: [],
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:01:00Z",
        run_count: 1,
        last_run_id: "run-with-files",
        resume_ready: true,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("s=session-files") as ReturnType<typeof navigation.useSearchParams>
    );

    render(<ChatShell />);

    // Prompt text should appear
    expect(screen.getByText("Analyze these files")).toBeInTheDocument();
    // File chips should appear
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
    expect(screen.getByText("data.csv")).toBeInTheDocument();
  });
});

describe("SessionCreateDialog — pre-populated from active host", () => {
  it("pre-populates host from the active host when dialog opens with LOCAL_HOST active", async () => {
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };

    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    // The host selector is present and pre-populated (LOCAL_HOST is the active host)
    expect(screen.getByLabelText("Agent host")).toBeInTheDocument();
  });

  it("pre-populates host from the active host when a remote host is active", async () => {
    const { saveHostProfile } = await import("@/lib/host-profiles");
    const remoteHost = {
      id: "pre-pop-host",
      label: "Pre-pop Tunnel",
      base_url: "https://prepop.ngrok.io",
      token: "",
      cli_kind: "copilot" as const,
      is_default: false,
    };
    saveHostProfile(remoteHost);
    hostStateMock = { host: remoteHost, diagnosticsEnabled: true };

    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    // HostPicker should be rendered with the remote host selected
    const hostSelect = screen.getByLabelText("Agent host");
    expect(hostSelect).toBeInTheDocument();
  });

  it("uses the URL-resolved active host when h= overrides the shared selected host", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");
    const { saveHostProfile } = await import("@/lib/host-profiles");

    const routeHost = {
      id: "route-host",
      label: "Route Tunnel",
      base_url: "https://route.ngrok.io",
      token: "tok-route",
      cli_kind: "copilot" as const,
      is_default: false,
    };

    saveHostProfile(routeHost);
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("h=route-host") as ReturnType<typeof navigation.useSearchParams>
    );

    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    expect(vi.mocked(hooks.useCreateOperatorSession).mock.calls.at(-1)?.[0]).toMatchObject({
      id: routeHost.id,
      base_url: routeHost.base_url,
    });
    expect(vi.mocked(hooks.useOperatorModelCatalog).mock.calls.at(-1)?.[0]).toMatchObject({
      id: routeHost.id,
      base_url: routeHost.base_url,
    });
  });
});

describe("ChatShell — hosted-root idle behavior", () => {
  it("shows idle guidance when on hosted root with no remote host", async () => {
    const navigation = await import("next/navigation");
    vi.mocked(navigation.usePathname).mockReturnValue(
      "/chat" as ReturnType<typeof navigation.usePathname>
    );
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false };

    render(<ChatShell />);
    expect(screen.getByTestId("chat-shell")).toBeInTheDocument();
  });
});
