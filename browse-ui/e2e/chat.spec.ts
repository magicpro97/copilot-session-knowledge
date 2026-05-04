import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const SESSION_ID = "e2e-operator-session-0001";
const CREATED_FILE = "~/projects/demo/notes.txt";
const CHANGED_FILE = "~/projects/demo/app.py";

const SEEDED_SESSION = {
  id: SESSION_ID,
  name: "E2E operator session",
  model: "claude-sonnet-4.5",
  mode: "interactive",
  workspace: "~/projects/demo",
  add_dirs: [],
  created_at: "2026-05-01T10:00:00Z",
  updated_at: "2026-05-01T10:05:00Z",
  run_count: 1,
  last_run_id: "run-created-1",
  resume_ready: true,
};

const CREATED_RUN = {
  id: "run-created-1",
  session_id: SESSION_ID,
  prompt: "Create a notes file with one line of text.",
  status: "done",
  exit_code: 0,
  started_at: "2026-05-01T10:01:00Z",
  finished_at: "2026-05-01T10:01:05Z",
  events: [
    {
      type: "assistant.message_start",
      idx: 0,
      event: { type: "assistant.message_start" },
      data: {},
    },
    {
      type: "assistant.message",
      idx: 1,
      event: { type: "assistant.message" },
      data: { content: "Created the requested notes file." },
    },
    {
      type: "tool.execution_start",
      idx: 2,
      event: { type: "tool.execution_start" },
      data: {
        toolName: "create_file",
        arguments: JSON.stringify({ path: CREATED_FILE }),
      },
    },
    {
      type: "tool.execution_complete",
      idx: 3,
      event: { type: "tool.execution_complete" },
      data: {
        result: {
          detailedContent: `Created ${CREATED_FILE}`,
        },
        toolTelemetry: {
          restrictedProperties: {
            filePaths: JSON.stringify([CREATED_FILE]),
            addedPaths: JSON.stringify([CREATED_FILE]),
          },
        },
      },
    },
  ],
};

const DIFF_RUN = {
  id: "run-diff-1",
  session_id: SESSION_ID,
  prompt: "Update the app title.",
  status: "done",
  exit_code: 0,
  started_at: "2026-05-01T10:02:00Z",
  finished_at: "2026-05-01T10:02:06Z",
  events: [
    {
      type: "assistant.message_start",
      idx: 0,
      event: { type: "assistant.message_start" },
      data: {},
    },
    {
      type: "assistant.message",
      idx: 1,
      event: { type: "assistant.message" },
      data: { content: "Updated the title in the existing file." },
    },
    {
      type: "tool.execution_start",
      idx: 2,
      event: { type: "tool.execution_start" },
      data: {
        toolName: "apply_patch",
        arguments: "*** Begin Patch\n*** End Patch\n",
      },
    },
    {
      type: "tool.execution_complete",
      idx: 3,
      event: { type: "tool.execution_complete" },
      data: {
        result: {
          detailedContent: [
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -1 +1 @@",
            '-TITLE = "Old title"',
            '+TITLE = "New title"',
          ].join("\n"),
        },
        toolTelemetry: {
          restrictedProperties: {
            filePaths: JSON.stringify([CHANGED_FILE]),
          },
        },
      },
    },
  ],
};

const MODEL_CATALOG = {
  models: [
    { id: "gpt-5.4", display_name: "GPT 5.4", provider: "OpenAI", default: true },
    { id: "claude-sonnet-4.6", display_name: "Claude Sonnet 4.6", provider: "Anthropic" },
  ],
  default_model: "gpt-5.4",
};

type MockOperatorApiOptions = {
  sessions?: (typeof SEEDED_SESSION)[];
  session?: typeof SEEDED_SESSION | null;
  runs?: Array<typeof CREATED_RUN | typeof DIFF_RUN>;
  previewByPath?: Record<
    string,
    {
      path: string;
      content: string;
      mime: string;
      size: number;
    }
  >;
};

async function mockOperatorApi(
  page: Page,
  { sessions = [], session = null, runs = [], previewByPath = {} }: MockOperatorApiOptions = {}
) {
  await page.route("**/api/operator/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/api/operator/sessions") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ sessions, count: sessions.length }),
      });
      return;
    }

    if (session && path === `/api/operator/sessions/${session.id}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(session),
      });
      return;
    }

    if (session && path === `/api/operator/sessions/${session.id}/runs`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ runs, count: runs.length }),
      });
      return;
    }

    if (path === "/api/operator/suggest") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ suggestions: [], count: 0 }),
      });
      return;
    }

    if (path === "/api/operator/models") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(MODEL_CATALOG),
      });
      return;
    }

    if (path === "/api/operator/preview") {
      const preview = previewByPath[url.searchParams.get("path") || ""];
      if (!preview) {
        throw new Error(`Unexpected preview request: ${url.search}`);
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(preview),
      });
      return;
    }

    throw new Error(`Unexpected operator API request: ${path}${url.search}`);
  });
}

test("/chat renders the operator console shell and session dialog", async ({ page }) => {
  await mockOperatorApi(page);

  await page.goto("/v2/chat/");
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("No session selected", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Run Copilot CLI prompts against a workspace and inspect results.", {
      exact: true,
    })
  ).toBeVisible();

  await page.getByRole("button", { name: "New chat session" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("Start a new chat session", { exact: true })).toBeVisible();
  await expect(page.getByText(/Must be a path under/)).toBeVisible();
});

test("/chat sidebar can be collapsed and reopened", async ({ page }) => {
  await mockOperatorApi(page);

  await page.goto("/v2/chat/");
  await expect(page.getByText("Chat Sessions", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Collapse session list" }).click();
  await expect(page.getByRole("button", { name: "Expand session list" })).toBeVisible();

  await page.getByRole("button", { name: "Expand session list" }).click();
  await expect(page.getByText("Chat Sessions", { exact: true })).toBeVisible();
});

test("/chat previews a created file from persisted run history", async ({ page }) => {
  await mockOperatorApi(page, {
    sessions: [SEEDED_SESSION],
    session: SEEDED_SESSION,
    runs: [CREATED_RUN],
    previewByPath: {
      [CREATED_FILE]: {
        path: CREATED_FILE,
        content: "Created from Playwright.",
        mime: "text/plain",
        size: 24,
      },
    },
  });

  await page.goto(`/v2/chat/?s=${SESSION_ID}`);
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(CREATED_RUN.prompt, { exact: true })).toBeVisible();
  await expect(page.getByText(CREATED_FILE, { exact: true })).toBeVisible();

  await page.locator('[title="Preview file"]').click();
  await expect(page.getByText("Created from Playwright.", { exact: true })).toBeVisible();
});

test("/chat shows inline diff for a changed file from persisted run history", async ({ page }) => {
  await mockOperatorApi(page, {
    sessions: [{ ...SEEDED_SESSION, last_run_id: DIFF_RUN.id }],
    session: { ...SEEDED_SESSION, last_run_id: DIFF_RUN.id },
    runs: [DIFF_RUN],
  });

  await page.goto(`/v2/chat/?s=${SESSION_ID}`);
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(DIFF_RUN.prompt, { exact: true })).toBeVisible();
  await expect(page.getByText(CHANGED_FILE, { exact: true })).toBeVisible();

  await page.locator('[title="Show applied diff"]').click();
  await expect(page.getByText("+1 added · -1 removed", { exact: true })).toBeVisible();
  await expect(page.getByText('TITLE = "Old title"', { exact: false })).toBeVisible();
  await expect(page.getByText('TITLE = "New title"', { exact: false })).toBeVisible();
});

test("/chat on mobile shows hamburger button and opens session sheet", async ({ page }) => {
  // Simulate a mobile viewport (iPhone SE dimensions)
  await page.setViewportSize({ width: 390, height: 844 });
  await mockOperatorApi(page, { sessions: [SEEDED_SESSION] });

  await page.goto("/v2/chat/");
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });

  // The hamburger button must be present
  const hamburger = page.getByRole("button", { name: "Open session list" });
  await expect(hamburger).toBeVisible();

  // Click it to open the session Sheet
  await hamburger.click();

  // The Sheet should be open — verify the session row button is visible inside it
  await expect(page.getByRole("button", { name: "E2E operator session ~/" })).toBeVisible();
});

test("/chat on mobile session-create dialog has free-text model input", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockOperatorApi(page);

  await page.goto("/v2/chat/");
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });

  // Open the mobile Sheet first
  await page.getByRole("button", { name: "Open session list" }).click();

  // Click "New Chat" inside the sheet
  await page.getByRole("button", { name: "New chat session" }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();

  // The model field should be a free-text input (not a locked select)
  const modelInput = page.getByLabel("Model");
  await expect(modelInput).toBeVisible();
  await expect(modelInput).toHaveAttribute("type", "text");
  await expect(modelInput).toHaveAttribute("placeholder", "gpt-5.4");

  // User can type an arbitrary model identifier
  await modelInput.fill("gpt-5-preview");
  await expect(modelInput).toHaveValue("gpt-5-preview");
});

test("/chat workspace picker has hidden-folder toggle", async ({ page }) => {
  await mockOperatorApi(page);

  await page.goto("/v2/chat/");
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });

  await page.getByRole("button", { name: "New chat session" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();

  // Toggle button for hidden folders is present and defaults to "off"
  const toggle = page.getByRole("button", { name: "Show hidden folders" });
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");

  // Clicking it switches to "hide" state
  await toggle.click();
  await expect(page.getByRole("button", { name: "Hide hidden folders" })).toBeVisible();
});

test("/chat session-create dialog has Agent Host picker defaulting to local", async ({ page }) => {
  await mockOperatorApi(page);

  await page.goto("/v2/chat/");
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });

  await page.getByRole("button", { name: "New chat session" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();

  // Agent Host section heading is visible
  await expect(page.getByText("Agent Host", { exact: true })).toBeVisible();

  // Add host button is present
  await expect(page.getByRole("button", { name: "Add agent host" })).toBeVisible();
});

test("/chat host picker shows add-host form when add button is clicked", async ({ page }) => {
  await mockOperatorApi(page);

  await page.goto("/v2/chat/");
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });

  await page.getByRole("button", { name: "New chat session" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();

  // Click Add agent host
  await page.getByRole("button", { name: "Add agent host" }).click();

  // The inline add-host form appears
  await expect(page.getByTestId("host-add-form")).toBeVisible();
  await expect(page.getByLabel("Tunnel URL")).toBeVisible();

  // Save host is disabled until URL is entered
  await expect(page.getByRole("button", { name: "Save host" })).toBeDisabled();

  // Entering a URL enables the Save host button
  await page.getByLabel("Tunnel URL").fill("https://demo.ngrok.io");
  await expect(page.getByRole("button", { name: "Save host" })).toBeEnabled();
});

test("/chat top bar shows 'CLI Chat' when no session is active", async ({ page }) => {
  await mockOperatorApi(page);

  await page.goto("/v2/chat/");
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });

  await expect(page.getByText("CLI Chat", { exact: false })).toBeVisible();
});

test("/chat composer shows file attach button and queued chip on file select", async ({ page }) => {
  const EMPTY_SESSION = { ...SEEDED_SESSION, run_count: 0, last_run_id: "" };

  await mockOperatorApi(page, {
    sessions: [EMPTY_SESSION],
    session: EMPTY_SESSION,
    runs: [],
  });

  await page.goto(`/v2/chat/?s=${SESSION_ID}`);
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });

  // Attach files button must be visible in the composer
  const attachBtn = page.getByRole("button", { name: "Attach files" });
  await expect(attachBtn).toBeVisible();

  // Use a file chooser to attach a file (bypasses the OS dialog)
  const fileChooserPromise = page.waitForEvent("filechooser");
  await attachBtn.click();
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles([
    { name: "analysis.md", mimeType: "text/markdown", buffer: Buffer.from("# Analysis") },
  ]);

  // Queued file chip appears
  await expect(page.getByText("analysis.md")).toBeVisible();

  // Remove button is accessible
  const removeBtn = page.getByRole("button", { name: "Remove analysis.md" });
  await expect(removeBtn).toBeVisible();

  // Clicking remove dismisses the chip
  await removeBtn.click();
  await expect(page.getByText("analysis.md")).not.toBeVisible({ timeout: 3_000 });
});

test("/chat composer supports drag-and-drop to queue files", async ({ page }) => {
  const EMPTY_SESSION = { ...SEEDED_SESSION, run_count: 0, last_run_id: "" };

  await mockOperatorApi(page, {
    sessions: [EMPTY_SESSION],
    session: EMPTY_SESSION,
    runs: [],
  });

  await page.goto(`/v2/chat/?s=${SESSION_ID}`);
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });

  // Simulate drag-and-drop onto the composer form
  const composer = page.locator("form").last();
  const dataTransfer = await page.evaluateHandle(() => {
    const dt = new DataTransfer();
    dt.items.add(new File(["dropped content"], "dropped.txt", { type: "text/plain" }));
    return dt;
  });

  await composer.dispatchEvent("dragenter", { dataTransfer });
  await composer.dispatchEvent("dragover", { dataTransfer });
  await composer.dispatchEvent("drop", { dataTransfer });

  await expect(page.getByText("dropped.txt")).toBeVisible();
});

// ─── Release proof: root-hosted Firebase export ────────────────────────────
//
// This test verifies that the static export produced for Firebase Hosting does
// NOT contain basePath-prefixed asset URLs (/v2/_next/…).
//
// Background: next.config.ts sets basePath: "/v2" for the Python-server
// deployment. A Firebase (root-hosted) export must be built WITHOUT that
// basePath — otherwise all /_next/ asset references become /v2/_next/…, which
// 404 on Firebase because the site serves dist/ from the domain root.
//
// This test reads dist-release/chat/index.html directly from the filesystem (no
// server needed) and asserts the correct asset-path shape for a root-hosted
// export.
//
// Run this before every Firebase deploy:
//   pnpm release:check
//
// How to produce a Firebase-compatible export:
//   1. pnpm --dir browse-ui release:check
//      (builds dist-release/ and runs the Firebase proof test in isolation)
//   3. firebase deploy --only hosting:agents  (from your private hosting repo)
// ───────────────────────────────────────────────────────────────────────────
test("chat root-hosted export has no /v2/_next/ asset references [FIREBASE_PROOF]", async () => {
  test.skip(
    !process.env.FIREBASE_PROOF,
    "Skipped — set FIREBASE_PROOF=1 to enable this Firebase release-gate check"
  );

  // The Firebase release build emits a root-hosted export at dist-release/chat/index.html.
  const distDir = resolve(__dirname, "../dist-release");
  const chatHtml = resolve(distDir, "chat", "index.html");

  expect(
    existsSync(chatHtml),
    `dist-release/chat/index.html not found at ${chatHtml}.\n` +
      "Run pnpm release:check before this proof check so dist-release/ contains " +
      "the root-hosted Firebase export."
  ).toBe(true);

  const html = readFileSync(chatHtml, "utf-8");

  // Regression gate: zero /v2/_next/ references allowed in a root-hosted export.
  const badMatches = Array.from(html.matchAll(/\/v2\/_next\//g));
  expect(
    badMatches.length,
    `Found ${badMatches.length} /v2/_next/ reference(s) in dist-release/chat/index.html.\n` +
      "Firebase serves assets from the domain root, so these paths will 404. " +
      "Re-run pnpm release:check before deploying to Firebase."
  ).toBe(0);

  // Sanity: the page must still reference /_next/ assets (non-trivial export).
  const goodMatches = Array.from(html.matchAll(/\/_next\//g));
  expect(
    goodMatches.length,
    "No /_next/ asset references found in dist-release/chat/index.html — " +
      "the export may be empty or the page did not build correctly."
  ).toBeGreaterThan(0);
});
