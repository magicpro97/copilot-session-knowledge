import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import {
  aliasPlaceholderSession,
  assertSeededSessionAvailable,
  SEEDED_SESSION_ID,
  stubEmptyFlightRecorderRoutes,
} from "./session-detail-alias";

const WORKFLOW_HEALTH_FIXTURE = {
  findings: [
    {
      id: "heavy-sessions",
      title: "Heavy sessions need summarization",
      detail: "2 sessions crossed the event budget without fresh checkpoints.",
      severity: "warning",
      impact: "Long sessions become hard to reuse.",
      action: "Run distill on sessions that exceed the event budget.",
    },
  ],
  health_grade: "B",
  generated_at: "2026-05-01T00:00:00Z",
};

const RETRO_BEHAVIOR_FIXTURE = {
  retro_score: 72,
  grade: "Good",
  grade_emoji: "🟢",
  mode: "repo",
  generated_at: "2026-05-01T00:00:00Z",
  available_sections: ["knowledge"],
  weights: { knowledge: 1 },
  subscores: { knowledge: 72 },
  knowledge: { health_score: 72 },
  skills: null,
  hooks: null,
  git: { commits: 4 },
  summary: "Recent sessions are yielding reusable knowledge, but completion habits can improve.",
  score_confidence: "medium",
  distortion_flags: [],
  accuracy_notes: [],
  improvement_actions: ["Add checkpoints earlier in long sessions."],
  behavior: {
    completion_rate: 0.75,
    knowledge_yield: 1.5,
    efficiency_ratio: 0.4,
    one_shot_rate: 0.5,
    session_count: 4,
    sessions_with_checkpoints: 3,
  },
};

const KNOWLEDGE_INSIGHTS_FIXTURE = {
  generated_at: "2026-05-01T00:00:00Z",
  summary: "Knowledge coverage is healthy enough to render diagnostics.",
  overview: {
    health_score: 82,
    total_entries: 120,
    sessions: 18,
    high_confidence_pct: 70,
    low_confidence_pct: 8,
    stale_pct: 6,
    relation_density: 2.4,
    embedding_pct: 78,
  },
  quality_alerts: [],
  recommended_actions: [],
  recurring_noise_titles: [],
  hot_files: [],
  entries: { mistakes: [], patterns: [], decisions: [], tools: [] },
};

const EMPTY_MISSION_ATLAS_PAYLOAD = JSON.stringify({
  schema_version: "1",
  session_id: SEEDED_SESSION_ID,
  total_events: 0,
  event_file_bytes: null,
  first_event_at: null,
  last_event_at: null,
  duration_ms: null,
  bucket_count: 0,
  buckets: [],
  lane_totals: {
    tool: 0,
    hook: 0,
    skill: 0,
    subagent: 0,
    model: 0,
    turn: 0,
    system: 0,
    error: 0,
    generic: 0,
  },
  top_tools: [],
  top_skills: [],
  top_agent_names: [],
  milestones: [],
  artifact_counts: {
    checkpoint_files: 0,
    rewind_snapshots: 0,
    todos_total: 0,
    todos_done: 0,
    todos_blocked: 0,
    todo_deps: 0,
    files: 0,
    compactions: 0,
  },
  error_count: 0,
  error_sample: [],
  caps: { top_n: 20, milestones: 200, error_sample: 5, buckets_min: 10, buckets_max: 200 },
  truncated: { tools: false, skills: false, agents: false, milestones: false },
});

async function stubEmptyMissionAtlas(page: Page): Promise<void> {
  await page.route(`**/api/session/${SEEDED_SESSION_ID}/mission-atlas*`, async (route) => {
    await route.fulfill({ contentType: "application/json", body: EMPTY_MISSION_ATLAS_PAYLOAD });
  });
}

test.beforeEach(async ({ page }) => {
  await stubEmptyFlightRecorderRoutes(page);
  await page.route("**/api/knowledge/insights*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(KNOWLEDGE_INSIGHTS_FIXTURE),
    });
  });
});

test("root routes render expected headings", async ({ page }) => {
  const headingByRoute = [
    ["/sessions/", "Sessions"],
    ["/search/", "Search"],
    ["/insights/", "Insights"],
    ["/graph/", "Graph"],
    ["/settings/", "Settings"],
  ] as const;

  for (const [route, heading] of headingByRoute) {
    await page.goto(route);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible({
      timeout: 20_000,
    });
  }

  await expect(page.getByText("Appearance & preferences", { exact: true })).toBeVisible();
  await expect(page.getByText("System health", { exact: true })).toBeVisible();
  await expect(page.getByText("Keyboard shortcuts reference", { exact: true })).toBeVisible();
});

test("sidebar navigation updates the top route subtitle", async ({ page }) => {
  const routeExpectations = [
    ["Search", "Query extracted knowledge and jump directly to matching sessions."],
    ["Insights", "Track knowledge trends, live feed status, and evaluation health."],
    ["Graph", "Explore relationships and embedding clusters in one network workspace."],
    ["Settings", "Tune preferences, diagnostics, and shortcut references."],
    ["Sessions", "Review indexed sessions and drill into details quickly."],
  ] as const;

  await page.goto("/sessions/");
  await expect(
    page.getByText("Review indexed sessions and drill into details quickly.", { exact: true })
  ).toBeVisible({
    timeout: 20_000,
  });

  for (const [label, subtitle] of routeExpectations) {
    await page.getByRole("link", { name: label, exact: true }).click();
    await expect(page.getByText(subtitle, { exact: true })).toBeVisible({
      timeout: 20_000,
    });
  }
});

test("session detail route renders tabbed UI", async ({ page }) => {
  await aliasPlaceholderSession(page);
  await page.goto("/sessions/_placeholder/");
  await expect(page).toHaveURL(/\/sessions\/_placeholder\/?(#overview)?$/);

  await expect(
    page.getByRole("main").getByLabel("Breadcrumb").getByRole("link", { name: "Sessions" })
  ).toBeVisible();
  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Timeline" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Mindmap" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Checkpoints" })).toBeVisible();
});

test("direct real UUID session detail route renders tabbed UI", async ({ page }) => {
  await assertSeededSessionAvailable(page);

  // Stub debug-log and mission-atlas so runtimeErrorGuard does not catch a 404
  // when the Timeline tab loads event-derived panels. The e2e session has no
  // events.jsonl on disk, so these endpoints return 404 by design.
  await stubEmptyMissionAtlas(page);

  // Returning an empty-entries 200 keeps the Timeline smoke test honest (shows empty
  // state) without suppressing real regressions.
  const emptyDebugLogPayload = JSON.stringify({
    schema_version: "debug-log/1",
    session_id: SEEDED_SESSION_ID,
    from: 0,
    limit: 5000,
    total: 0,
    has_more: false,
    entries: [],
  });
  await page.route(`**/api/session/${SEEDED_SESSION_ID}/debug-log*`, async (route) => {
    await route.fulfill({ contentType: "application/json", body: emptyDebugLogPayload });
  });

  const placeholderSessionRequests: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes("/api/sessions/_placeholder") || url.includes("/api/session/_placeholder/")) {
      placeholderSessionRequests.push(url);
    }
  });
  // Operator CLI-session endpoint must not fire when no operator token is present.
  const cliSessionRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/operator/cli-sessions")) {
      cliSessionRequests.push(request.url());
    }
  });

  await page.goto(`/sessions/${SEEDED_SESSION_ID}/`);
  await expect(page).toHaveURL(new RegExp(`/sessions/${SEEDED_SESSION_ID}/?(#overview)?$`));
  await expect(page.getByText("Failed to load session detail", { exact: true })).toHaveCount(0);

  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Timeline" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Mindmap" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Checkpoints" })).toBeVisible();
  await page.getByRole("tab", { name: "Timeline" }).click();
  await page.getByRole("tab", { name: "Mindmap" }).click();
  await expect(page.getByRole("button", { name: "Zoom In" })).toBeEnabled({
    timeout: 20_000,
  });
  await page.getByRole("button", { name: "Zoom In" }).click();
  await page.getByRole("button", { name: "Zoom Out" }).click();
  const invalidTransforms = await page
    .locator('svg[aria-label="Session mindmap"] [transform]')
    .evaluateAll((nodes) =>
      nodes
        .map((node) => node.getAttribute("transform") || "")
        .filter((transform) => /NaN|Infinity/.test(transform))
    );
  expect(invalidTransforms).toEqual([]);
  await page.getByRole("tab", { name: "Checkpoints" }).click();
  await page.waitForLoadState("networkidle");
  expect(placeholderSessionRequests).toEqual([]);
  expect(cliSessionRequests).toEqual([]);
});

test("session debug log renders flow chart from CLI hierarchy data", async ({ page }) => {
  await assertSeededSessionAvailable(page);
  await stubEmptyMissionAtlas(page);

  await page.route(`**/api/session/${SEEDED_SESSION_ID}/debug-log*`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "debug-log/1",
        session_id: SEEDED_SESSION_ID,
        from: 0,
        limit: 100,
        total: 3,
        has_more: false,
        entries: [
          {
            idx: 0,
            timestamp: "2026-05-01T00:00:00.000Z",
            kind: "turn_start",
            level: "info",
            source: "copilot-cli",
            message: "Turn started",
            tool_name: null,
            duration_ms: 240,
            span_id: "turn-root",
            parent_span_id: null,
            status: "ok",
            attrs: null,
            redacted: false,
          },
          {
            idx: 1,
            timestamp: "2026-05-01T00:00:00.050Z",
            kind: "tool_call",
            level: "debug",
            source: "tool",
            message: "Read debug event file",
            tool_name: "view",
            duration_ms: 90,
            span_id: "tool-child",
            parent_span_id: "turn-root",
            status: "ok",
            attrs: null,
            redacted: false,
          },
          {
            idx: 2,
            timestamp: "2026-05-01T00:00:00.120Z",
            kind: "hook",
            level: "info",
            source: "hook",
            message: "Post tool hook completed",
            tool_name: null,
            duration_ms: 30,
            span_id: "hook-child",
            parent_span_id: "tool-child",
            status: "ok",
            attrs: null,
            redacted: false,
          },
        ],
      }),
    });
  });

  await page.goto(`/sessions/${SEEDED_SESSION_ID}/#debug-log`);
  await expect(page.getByRole("tab", { name: "Debug Log" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId("debug-log-view-flow")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("debug-log-view-flow").click();

  await expect(page.getByTestId("debug-log-flow-chart")).toBeVisible();
  await expect(page.getByLabel("Debug log flow chart")).toContainText("Turn started");
  await expect(page.getByLabel("Debug log flow chart")).toContainText("Read debug event file");
  await expect(page.getByLabel("Debug log flow chart")).toContainText("Post tool hook completed");
});

// Issue #536: Browse debug-log Playwright proof for zoom safety + rich Flow content.
//
// Acceptance:
//   * Fixture covers tool/hook/skill/assistant(model)/subagent rich Flow content
//     with `has_more: true` so the renderer shows its "More events available" hint.
//   * Console is captured; the test fails if Chrome ever logs the
//     "Unable to preventDefault inside passive event listener invocation" warning
//     (regression signal for the non-passive wheel-listener fix).
//   * Mouse wheel over the SVG updates the zoom percentage text rendered by
//     `data-testid="debug-log-flow-zoom"` — proves the wheel listener actually
//     drives zoom (and is therefore non-passive).
//   * Rich labels / sublabels / timestamps from `node.render` are visible.
test("session debug log flow chart: zoom safety + rich content (issue #536)", async ({ page }) => {
  await assertSeededSessionAvailable(page);

  // Capture all console messages so we can assert no passive-listener warning
  // is ever emitted. We must register BEFORE navigation.
  const consoleMessages: { type: string; text: string }[] = [];
  page.on("console", (msg) => {
    consoleMessages.push({ type: msg.type(), text: msg.text() });
  });
  // Capture uncaught page errors as well — a thrown wheel handler would
  // be a separate regression mode.
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => {
    pageErrors.push(error.message);
  });
  let watchBucketNavigation = false;
  let bucketClickNavigationCount = 0;
  page.on("framenavigated", (frame) => {
    if (watchBucketNavigation && frame === page.mainFrame()) {
      bucketClickNavigationCount += 1;
    }
  });

  // Synthetic, redaction-safe debug-log payload covering the full rich-Flow
  // contract surface: turn, tool, hook, skill, assistant (agent_response /
  // model), subagent. has_more=true exercises the "More events available"
  // affordance the renderer exposes via data-testid="debug-log-flow-has-more".
  await page.route(`**/api/session/${SEEDED_SESSION_ID}/mission-atlas*`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "1",
        session_id: SEEDED_SESSION_ID,
        total_events: 550,
        event_file_bytes: 180000,
        first_event_at: "2026-05-01T01:02:03.000Z",
        last_event_at: "2026-05-01T01:12:03.000Z",
        duration_ms: 600000,
        bucket_count: 2,
        buckets: [
          {
            bucket_idx: 0,
            start_idx: 0,
            end_idx: 99,
            event_count: 100,
            start_rel_ms: 0,
            end_rel_ms: 300000,
            ts_start: "2026-05-01T01:02:03.000Z",
            ts_end: "2026-05-01T01:07:03.000Z",
            lanes: { turn: 1, tool: 40, hook: 20, skill: 10, model: 20, subagent: 9 },
            dominant_lane: "tool",
            error_count: 0,
            is_gap: false,
          },
          {
            bucket_idx: 1,
            start_idx: 100,
            end_idx: 199,
            event_count: 100,
            start_rel_ms: 300001,
            end_rel_ms: 600000,
            ts_start: "2026-05-01T01:07:03.001Z",
            ts_end: "2026-05-01T01:12:03.000Z",
            lanes: { tool: 30, model: 50, subagent: 20 },
            dominant_lane: "model",
            error_count: 0,
            is_gap: false,
          },
        ],
        lane_totals: {
          tool: 70,
          hook: 20,
          skill: 10,
          subagent: 29,
          model: 70,
          turn: 1,
          system: 0,
          error: 0,
          generic: 0,
        },
        top_tools: [{ name: "view", count: 40 }],
        top_skills: [{ name: "code-reviewer", count: 10 }],
        top_agent_names: [{ name: "research-agent", count: 9 }],
        milestones: [
          {
            idx: 100,
            timestamp: "2026-05-01T01:07:03.001Z",
            kind: "checkpoint",
            label: "Cross-page checkpoint",
            bucket_idx: 1,
          },
        ],
        artifact_counts: {
          checkpoint_files: 1,
          rewind_snapshots: 0,
          todos_total: 0,
          todos_done: 0,
          todos_blocked: 0,
          todo_deps: 0,
          files: 0,
          compactions: 0,
        },
        error_count: 0,
        error_sample: [],
        caps: { top_n: 20, milestones: 200, error_sample: 5, buckets_min: 10, buckets_max: 200 },
        truncated: { tools: false, skills: false, agents: false, milestones: false },
      }),
    });
  });

  await page.route(`**/api/session/${SEEDED_SESSION_ID}/debug-log*`, async (route) => {
    const url = new URL(route.request().url());
    const from = Number(url.searchParams.get("from") ?? "0");
    const limit = Number(url.searchParams.get("limit") ?? "100");
    // Flow view fetches/appends 100-event server pages; List/Tree remains page-by-page too.
    const isPageFetchAfterFirst = from >= 100;
    if (isPageFetchAfterFirst) {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    const page2Entry = {
      idx: 100,
      timestamp: "2026-05-01T01:07:03.001Z",
      kind: "tool_call",
      level: "debug",
      source: "tool",
      message: "Cross-page drilldown target",
      tool_name: "bash",
      duration_ms: 80,
      span_id: "page-two-tool",
      parent_span_id: null,
      status: "ok",
      attrs: { tool_status: "ok", tool_result_type: "text" },
      redacted: false,
    };
    const page1Entries = [
      {
        idx: 0,
        timestamp: "2026-05-01T01:02:03.000Z",
        kind: "turn_start",
        level: "info",
        source: "copilot-cli",
        message: "Turn started",
        tool_name: null,
        duration_ms: 240,
        span_id: "turn-root",
        parent_span_id: null,
        status: "ok",
        attrs: { mode: "agent" },
        redacted: false,
      },
      {
        idx: 1,
        timestamp: "2026-05-01T01:02:03.100Z",
        kind: "tool_call",
        level: "debug",
        source: "tool",
        message: "Read debug event file",
        tool_name: "view",
        duration_ms: 120,
        span_id: "tool-view",
        parent_span_id: "turn-root",
        status: "ok",
        attrs: {
          tool_status: "ok",
          tool_result_type: "text",
          tool_metric_duration_ms: 120,
        },
        redacted: false,
      },
      {
        idx: 2,
        timestamp: "2026-05-01T01:02:03.200Z",
        kind: "hook",
        level: "info",
        source: "hook",
        message: "Post tool hook completed",
        tool_name: null,
        duration_ms: 30,
        span_id: "hook-post",
        parent_span_id: "tool-view",
        status: "ok",
        attrs: {
          hook_type: "post-tool-use",
          hook_status: "ok",
          event_phase: "complete",
        },
        redacted: false,
      },
      {
        idx: 3,
        timestamp: "2026-05-01T01:02:03.300Z",
        kind: "skill_run",
        level: "info",
        source: "skill",
        message: "Skill executed",
        tool_name: null,
        duration_ms: 75,
        span_id: "skill-cr",
        parent_span_id: "turn-root",
        status: "ok",
        attrs: {
          skill_name: "code-reviewer",
          skill_path_category: "skill_pkg",
          skill_content_bytes: 4096,
        },
        redacted: false,
      },
      {
        idx: 4,
        timestamp: "2026-05-01T01:02:03.400Z",
        kind: "agent_response",
        level: "info",
        source: "copilot-cli",
        message: "Assistant produced a response",
        tool_name: null,
        duration_ms: 200,
        span_id: "model-assistant",
        parent_span_id: "turn-root",
        status: "ok",
        attrs: {
          output_tokens: 42,
          tool_request_count: 1,
        },
        redacted: false,
      },
      {
        idx: 5,
        timestamp: "2026-05-01T01:02:03.500Z",
        kind: "subagent",
        level: "info",
        source: "copilot-cli",
        message: "research subagent",
        tool_name: null,
        duration_ms: 60,
        span_id: "subagent-research",
        parent_span_id: "turn-root",
        status: "ok",
        attrs: null,
        redacted: false,
      },
    ];
    const entries = isPageFetchAfterFirst ? [page2Entry] : page1Entries;

    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "debug-log/1",
        session_id: SEEDED_SESSION_ID,
        from,
        limit,
        total: 550,
        has_more: from + entries.length < 550,
        entries,
      }),
    });
  });

  await page.goto(`/sessions/${SEEDED_SESSION_ID}/#debug-log`);
  await expect(page.getByRole("tab", { name: "Debug Log" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("debug-log-view-flow")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("debug-log-view-flow").click();

  const chart = page.getByTestId("debug-log-flow-chart");
  await expect(chart).toBeVisible();

  // -- Full-session Flow canvas (Mission Atlas aggregate) ----------------
  const sessionCanvas = page.getByTestId("debug-log-flow-session-canvas");
  await expect(sessionCanvas).toBeVisible();
  await expect(sessionCanvas).toHaveAttribute("data-flow-total-events", "550");
  await expect(page.getByTestId("debug-log-flow-session-bucket-tool-0")).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-session-bucket-tool-1")).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-session-bucket-model-1")).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-session-milestone-0")).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-session-tool-chip-view")).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-session-skill-chip-code-reviewer")).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-session-agent-chip-research-agent")).toBeVisible();

  // -- Rich Flow content from node.render -------------------------------
  // Primary labels (truncated to 28 chars by the renderer — all our
  // fixture labels fit comfortably under that limit).
  const flow = page.getByLabel("Debug log flow chart");
  await expect(flow).toContainText("Turn started"); // turn (message fallback)
  await expect(flow).toContainText("view"); // tool (tool_name)
  await expect(flow).toContainText("post-tool-use"); // hook (hook_type)
  await expect(flow).toContainText("code-reviewer"); // skill (skill_name)
  await expect(flow).toContainText("agent_response"); // model (assistant)
  await expect(flow).toContainText("research subagent"); // subagent (message)

  // Sublabels (status / duration / tokens / path category).
  await expect(flow).toContainText("ok · text"); // tool sublabel prefix
  await expect(flow).toContainText("ok · complete"); // hook sublabel
  await expect(flow).toContainText("skill_pkg"); // skill sublabel
  await expect(flow).toContainText("42 tok"); // model sublabel

  // Timestamp label (HH:MM:SS UTC) is rendered per-node.
  await expect(flow).toContainText("01:02:03");

  // Per-entry rich testids prove the render model fed every category.
  await expect(page.getByTestId("debug-log-flow-node-1-label")).toContainText("view");
  await expect(page.getByTestId("debug-log-flow-node-1-sublabel")).toContainText("ok");
  await expect(page.getByTestId("debug-log-flow-node-1-timestamp")).toContainText("01:02:03");
  await expect(page.getByTestId("debug-log-flow-node-2-label")).toContainText("post-tool-use");
  await expect(page.getByTestId("debug-log-flow-node-3-label")).toContainText("code-reviewer");
  await expect(page.getByTestId("debug-log-flow-node-4-label")).toContainText("agent_response");
  await expect(page.getByTestId("debug-log-flow-node-5-label")).toContainText("research subagent");

  // data-flow-status attribute is present on every rendered node.
  await expect(page.getByTestId("debug-log-flow-node-1")).toHaveAttribute("data-flow-status", "ok");
  await expect(page.getByTestId("debug-log-flow-node-2")).toHaveAttribute("data-flow-status", "ok");

  // SVG <title> tooltip body is reachable for the tool node.
  const toolNodeTitle = page.locator('[data-testid="debug-log-flow-node-1"] > title');
  await expect(toolNodeTitle).toHaveText(/kind: tool_call/);
  await expect(toolNodeTitle).toHaveText(/tool: view/);

  // -- v2 trace overview strip + inspector ------------------------------
  // The trace strip renders above the legacy tree and must coexist with it.
  const trace = page.getByTestId("debug-log-flow-trace");
  await expect(trace).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-time-ruler")).toBeVisible();
  // At least the two highest-traffic lanes for this fixture must be present.
  await expect(page.getByTestId("debug-log-flow-lane-tool-hook-skill")).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-lane-model")).toBeVisible();
  // Ruler must have >= 2 ticks (start + end at minimum).
  expect(
    await page.locator('[data-testid^="debug-log-flow-tick-"]').count()
  ).toBeGreaterThanOrEqual(2);

  // Clicking a tool bar opens the inspector card with the safe metadata rows
  // (and ALSO opens the existing DetailDrawer — selection contract preserved).
  await expect(page.getByTestId("debug-log-flow-inspector-empty")).toBeVisible();
  await page.getByTestId("debug-log-flow-bar-1").click();
  const inspector = page.getByTestId("debug-log-flow-inspector");
  await expect(inspector).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-inspector-status")).toHaveText(/ok/);
  await expect(page.getByTestId("debug-log-flow-bar-1")).toHaveAttribute("data-flow-status", "ok");
  await expect(page.getByTestId("debug-log-flow-bar-1")).toHaveAttribute(
    "data-flow-lane",
    "Tool/Hook/Skill"
  );

  // -- has_more affordance ----------------------------------------------
  await expect(page.getByTestId("debug-log-flow-has-more")).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-has-more")).toContainText(/More events available/);
  await expect(page.getByTestId("debug-log-flow-load-more")).toBeVisible();

  // -- Zoom safety: wheel listener must be non-passive ------------------
  const zoom = page.getByTestId("debug-log-flow-zoom");
  await expect(zoom).toBeVisible();
  await expect(zoom).toHaveText("100%");

  // Dispatch WheelEvents directly on the SVG via the DOM. This goes through
  // the same `addEventListener("wheel", ..., { passive: false })` path the
  // production component installs, so the regression intent (non-passive
  // listener actually drives zoom) is preserved. CDP's `page.mouse.wheel()`
  // does not reliably reach native non-passive listeners in headless
  // Chromium, which made this assertion flaky without losing the contract.
  const svg = page.locator('svg[aria-label="Debug log flow chart"]');
  await expect(svg).toBeVisible();
  const box = await svg.boundingBox();
  expect(box, "Flow chart SVG must have a layout box").not.toBeNull();

  // Two zoom-out wheel ticks; each: factor = 2^(-200 * 0.002) ≈ 0.758.
  // Combined ≈ 0.575, which rounds to "57%" — distinct from "100%".
  await svg.evaluate((el, box) => {
    const cx = box ? box.x + box.width / 2 : 0;
    const cy = box ? box.y + box.height / 2 : 0;
    for (let i = 0; i < 2; i += 1) {
      el.dispatchEvent(
        new WheelEvent("wheel", {
          deltaY: 200,
          deltaMode: 0,
          clientX: cx,
          clientY: cy,
          bubbles: true,
          cancelable: true,
        })
      );
    }
  }, box);

  // The zoom indicator must visibly change. If the listener regresses to
  // passive (or the handler stops calling setScale), this stays "100%".
  await expect(zoom).not.toHaveText("100%", { timeout: 5_000 });

  // Zoom in afterwards to prove both directions still work.
  await svg.evaluate((el, box) => {
    const cx = box ? box.x + box.width / 2 : 0;
    const cy = box ? box.y + box.height / 2 : 0;
    el.dispatchEvent(
      new WheelEvent("wheel", {
        deltaY: -400,
        deltaMode: 0,
        clientX: cx,
        clientY: cy,
        bubbles: true,
        cancelable: true,
      })
    );
  }, box);
  await expect(zoom).toHaveText(/\d+%/);

  // Aggregate click-through must fetch/append the target 100-event page and render node-100.
  // In flow mode, clicking a bucket does not use the List/Tree pagination controls.
  await page.getByLabel("Close detail panel").click();
  await expect(page.getByRole("dialog", { name: /debug event detail/i })).toHaveCount(0);
  watchBucketNavigation = true;
  await page.getByTestId("debug-log-flow-session-bucket-tool-1").click();
  await expect(sessionCanvas).toBeVisible();
  // The flow loading indicator appears while the extended fetch is in-flight.
  await expect(page.getByTestId("debug-log-flow-page-loading")).toBeVisible();
  // No legacy page navigation — the bucket click stays within Flow mode.
  expect(bucketClickNavigationCount).toBe(0);
  // After the extended fetch resolves, node-100 is rendered in the flow chart.
  await expect(page.getByTestId("debug-log-flow-node-100")).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-node-100-label")).toContainText("bash");

  // -- Console must be clean of the passive-listener warning ------------
  const passiveErrors = consoleMessages.filter((m) =>
    /Unable to preventDefault inside passive event listener invocation/i.test(m.text)
  );
  expect(
    passiveErrors,
    `Expected no passive-listener warnings, got: ${JSON.stringify(passiveErrors)}`
  ).toEqual([]);
  expect(pageErrors, `Unexpected page errors: ${JSON.stringify(pageErrors)}`).toEqual([]);
});

// Issue #542: Timeline Playback — Playwright e2e proof.
//
// Acceptance:
//   * Stubs /api/session/{id}/debug-log with a projection-aware handler:
//     - projection=skeleton  → BrowseDebugSkeletonEntry[] payload (5 entries)
//     - no projection / full → BrowseDebugEntry[] payload (same 5 entries, full fields)
//   * Navigates to #timeline, asserts player, waterfall, and lane rows visible.
//   * Play/Pause button toggles aria-label between "Play" and "Pause".
//   * Event card initially shows Event 0 (turn_start).
//   * Scrubber range change moves playhead → event card updates.
//   * btn-marker-next advances to next marker (agent_response).
//   * Waterfall bar click changes current event.
//   * "Open in Debug Log" navigates to #debug-log and selects the matching entry.
//   * runtimeErrorGuard (auto fixture) fails the test on any console.error/pageerror/API 4xx.
test("Timeline playback: player, waterfall, play/pause, scrub, marker, bar, Open in Debug Log (issue #542)", async ({
  page,
}) => {
  await assertSeededSessionAvailable(page);
  await stubEmptyMissionAtlas(page);

  // ── Redaction-safe skeleton entries (no message/attrs/source/level/tool_name) ──
  const TIMELINE_SKELETON_ENTRIES = [
    {
      idx: 0,
      timestamp: "2026-05-01T00:00:00.000Z",
      kind: "turn_start",
      duration_ms: 500,
      status: "ok",
      span_id: "span-t0",
      parent_span_id: null,
    },
    {
      idx: 1,
      timestamp: "2026-05-01T00:00:01.000Z",
      kind: "tool_call",
      duration_ms: 100,
      status: "ok",
      span_id: "span-tl1",
      parent_span_id: "span-t0",
    },
    {
      idx: 2,
      timestamp: "2026-05-01T00:00:02.000Z",
      kind: "agent_response",
      duration_ms: 200,
      status: "ok",
      span_id: "span-ar2",
      parent_span_id: "span-t0",
    },
    {
      idx: 3,
      timestamp: "2026-05-01T00:00:03.000Z",
      kind: "hook",
      duration_ms: 50,
      status: "ok",
      span_id: "span-h3",
      parent_span_id: "span-tl1",
    },
    {
      idx: 4,
      timestamp: "2026-05-01T00:00:04.000Z",
      kind: "turn_start",
      duration_ms: 600,
      status: "ok",
      span_id: "span-t4",
      parent_span_id: null,
    },
  ];

  const TIMELINE_SKELETON_PAYLOAD = {
    schema_version: "debug-log/1",
    session_id: SEEDED_SESSION_ID,
    from: 0,
    limit: 5000,
    total: 5,
    has_more: false,
    entries: TIMELINE_SKELETON_ENTRIES,
  };

  // ── Full BrowseDebugEntry payload for the Debug Log tab (no projection param) ──
  const TIMELINE_FULL_PAYLOAD = {
    schema_version: "debug-log/1",
    session_id: SEEDED_SESSION_ID,
    from: 0,
    limit: 50,
    total: 5,
    has_more: false,
    entries: [
      {
        idx: 0,
        timestamp: "2026-05-01T00:00:00.000Z",
        kind: "turn_start",
        level: "info",
        source: "copilot-cli",
        message: "Turn started",
        tool_name: null,
        duration_ms: 500,
        span_id: "span-t0",
        parent_span_id: null,
        status: "ok",
        attrs: null,
        redacted: false,
      },
      {
        idx: 1,
        timestamp: "2026-05-01T00:00:01.000Z",
        kind: "tool_call",
        level: "debug",
        source: "tool",
        message: "Read file contents",
        tool_name: "view",
        duration_ms: 100,
        span_id: "span-tl1",
        parent_span_id: "span-t0",
        status: "ok",
        attrs: null,
        redacted: false,
      },
      {
        idx: 2,
        timestamp: "2026-05-01T00:00:02.000Z",
        kind: "agent_response",
        level: "info",
        source: "copilot-cli",
        message: "Assistant produced response",
        tool_name: null,
        duration_ms: 200,
        span_id: "span-ar2",
        parent_span_id: "span-t0",
        status: "ok",
        attrs: null,
        redacted: false,
      },
      {
        idx: 3,
        timestamp: "2026-05-01T00:00:03.000Z",
        kind: "hook",
        level: "info",
        source: "hook",
        message: "Post-tool hook completed",
        tool_name: null,
        duration_ms: 50,
        span_id: "span-h3",
        parent_span_id: "span-tl1",
        status: "ok",
        attrs: null,
        redacted: false,
      },
      {
        idx: 4,
        timestamp: "2026-05-01T00:00:04.000Z",
        kind: "turn_start",
        level: "info",
        source: "copilot-cli",
        message: "Second turn started",
        tool_name: null,
        duration_ms: 600,
        span_id: "span-t4",
        parent_span_id: null,
        status: "ok",
        attrs: null,
        redacted: false,
      },
    ],
  };

  // ── Route: skeleton projection → skeleton payload; otherwise full payload ──
  // The Timeline tab fetches with ?projection=skeleton&from=0&limit=5000.
  // The Debug Log tab fetches without projection (uses session-scoped path).
  // Both share the same URL pattern; the handler discriminates on the query param.
  await page.route(`**/api/session/${SEEDED_SESSION_ID}/debug-log*`, async (route) => {
    const url = new URL(route.request().url());
    const isSkeleton = url.searchParams.get("projection") === "skeleton";
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(isSkeleton ? TIMELINE_SKELETON_PAYLOAD : TIMELINE_FULL_PAYLOAD),
    });
  });

  // ── Navigate to #timeline ────────────────────────────────────────────────
  await page.goto(`/sessions/${SEEDED_SESSION_ID}/#timeline`);

  // Timeline tab should be selected (hash sets activeTab on mount).
  await expect(page.getByRole("tab", { name: "Timeline" })).toHaveAttribute(
    "aria-selected",
    "true",
    { timeout: 20_000 }
  );

  // ── Player and waterfall visibility ──────────────────────────────────────
  const player = page.getByTestId("timeline-player");
  await expect(player).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("waterfall")).toBeVisible();

  // Expect lane rows for the entry kinds in our fixture:
  //   turn_start/turn_start → "Turn" lane
  //   tool_call/hook        → "Tool/Hook/Skill" lane
  //   agent_response        → "Model" lane
  await expect(page.getByTestId("lane-row-Turn")).toBeVisible();
  await expect(page.getByTestId("lane-row-Tool/Hook/Skill")).toBeVisible();
  await expect(page.getByTestId("lane-row-Model")).toBeVisible();

  // ── Initial event card shows Event 0 (turn_start) ────────────────────────
  const card = page.getByTestId("event-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Event 0");
  await expect(card).toContainText("turn_start");

  // ── Play / Pause toggle ───────────────────────────────────────────────────
  const playPauseBtn = page.getByTestId("btn-play-pause");
  await expect(playPauseBtn).toHaveAttribute("aria-label", "Play");
  await playPauseBtn.click();
  await expect(playPauseBtn).toHaveAttribute("aria-label", "Pause", { timeout: 5_000 });
  await playPauseBtn.click();
  await expect(playPauseBtn).toHaveAttribute("aria-label", "Play", { timeout: 5_000 });

  // ── Scrubber drag changes current event ──────────────────────────────────
  // Entries sorted by timestamp: idx 0→1→2→3→4 maps to sortedIndex 0→1→2→3→4.
  // Use keyboard ArrowRight/ArrowLeft on the focused scrubber to advance/retreat
  // the playhead. Native keyboard events reliably fire React onChange on range inputs.
  const scrubber = page.getByTestId("scrubber");
  await scrubber.focus();
  // Arrow right ×2: sortedIndex 0 → 1 → 2 (agent_response)
  await scrubber.press("ArrowRight");
  await scrubber.press("ArrowRight");
  await expect(card).toContainText("Event 2", { timeout: 5_000 });
  await expect(card).toContainText("agent_response");

  // Arrow left ×2: sortedIndex 2 → 1 → 0 (turn_start) — reset position.
  await scrubber.press("ArrowLeft");
  await scrubber.press("ArrowLeft");
  await expect(card).toContainText("Event 0", { timeout: 5_000 });

  // ── Marker navigation (btn-marker-next) ──────────────────────────────────
  // Markers are derived from entries with kind turn_start or agent_response:
  //   sortedIdx=0 (turn_start), sortedIdx=2 (agent_response), sortedIdx=4 (turn_start).
  // From sortedIdx=0, nextMarkerIndex should advance to sortedIdx=2 (agent_response).
  const markerNextBtn = page.getByTestId("btn-marker-next");
  await expect(markerNextBtn).toBeVisible();
  await markerNextBtn.click();
  await expect(card).toContainText("agent_response", { timeout: 5_000 });

  // ── Waterfall bar click changes current event ─────────────────────────────
  // Each bar button has aria-label "Event {idx}: {kind}".
  // Click the bar for entry idx=1 (tool_call) in the waterfall.
  await page.getByTestId("waterfall").getByLabel("Event 1: tool_call").click();
  await expect(card).toContainText("Event 1", { timeout: 5_000 });
  await expect(card).toContainText("tool_call");

  // ── "Open in Debug Log" navigates to #debug-log and selects entry ─────────
  // btn-open-debug-log is in the event card when onOpenDebugLog is wired.
  // Current event is idx=1 (tool_call).
  const openBtn = page.getByTestId("btn-open-debug-log");
  await expect(openBtn).toBeVisible();
  await openBtn.click();

  // URL hash must change to #debug-log.
  await expect(page).toHaveURL(/#debug-log/, { timeout: 10_000 });
  await expect(page.getByRole("tab", { name: "Debug Log" })).toHaveAttribute(
    "aria-selected",
    "true",
    { timeout: 10_000 }
  );

  // Effect 1 (focusEntryIdx=1) → navigates debug-log to page 0, clears filters.
  // Effect 2 → data loads (our stub fires immediately), finds idx=1, setSelectedEntry.
  // The selected row's aria-expanded becomes "true" once the state machine completes.
  await expect(page.locator('[aria-label="Debug event 1: tool_call from tool"]')).toHaveAttribute(
    "aria-expanded",
    "true",
    { timeout: 15_000 }
  );
});

test("sessions list click-through opens real UUID session detail", async ({ page }) => {
  await assertSeededSessionAvailable(page);
  await page.goto("/sessions/");
  await expect(page.getByRole("heading", { level: 1, name: "Sessions" })).toBeVisible({
    timeout: 20_000,
  });

  await expect(page.locator("tbody tr div.animate-pulse")).toHaveCount(0);
  const firstDataRow = page.locator("tbody tr").first();
  await expect(firstDataRow).toBeVisible();
  await firstDataRow.click();
  await expect(page).toHaveURL(/\/sessions\/[^/]+\/?(#overview)?$/);
  await expect(page).not.toHaveURL(/\/sessions\/_placeholder\/?(#overview)?$/);
  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible();
});

test("checkpoint diff viewer loads diff results and supports both modes", async ({ page }) => {
  await assertSeededSessionAvailable(page);
  await page.route("**/api/diff*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        session_id: SEEDED_SESSION_ID,
        from: { seq: 1, title: "Checkpoint 1", file: "checkpoint_001.md" },
        to: { seq: 3, title: "Checkpoint 3", file: "checkpoint_003.md" },
        unified_diff: [
          "--- checkpoint_001.md",
          "+++ checkpoint_003.md",
          "@@ -1,2 +1,2 @@",
          "-Removed detail",
          " context line",
          "+Added detail",
        ].join("\\n"),
        files: [{ from: "checkpoint_001.md", to: "checkpoint_003.md" }],
        stats: { added: 1, removed: 1 },
      }),
    });
  });

  await page.goto(`/sessions/${SEEDED_SESSION_ID}/#checkpoints`);
  await expect(page.getByRole("tab", { name: "Checkpoints" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await page.getByRole("button", { name: "Compute diff" }).click();

  await expect(page.getByText("Checkpoint diff (1 → 3)", { exact: true })).toBeVisible();
  await expect(page.getByText("+1 added · -1 removed", { exact: true })).toBeVisible();
  await expect(page.getByText("Added detail")).toBeVisible();
  await expect(page.getByText("Removed detail")).toBeVisible();

  await page.getByRole("button", { name: "Side-by-side" }).click();
  const splitSummary = await page.evaluate(() => ({
    left: document.querySelectorAll('[data-diff-side="left"]').length,
    right: document.querySelectorAll('[data-diff-side="right"]').length,
  }));
  expect(splitSummary.left).toBeGreaterThan(0);
  expect(splitSummary.right).toBeGreaterThan(0);
});

test("graph defaults to Insight tab and evidence/similarity tabs render live surfaces", async ({
  page,
}) => {
  await page.goto("/graph/");
  await expect(page.getByRole("heading", { level: 1, name: "Graph" })).toBeVisible({
    timeout: 20_000,
  });

  // New default: Insight tab is active
  await expect(page.getByRole("tab", { name: "Insight" })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/\/graph\/?#insight$/);

  // All four tabs must be present in the tab bar
  await expect(page.getByRole("tab", { name: "Insight" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Evidence" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Similarity" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Communities" })).toBeVisible();
  await expect(page.getByRole("tablist")).toHaveCSS("flex-direction", "column");
  // Legacy tab names must not appear as tabs
  await expect(page.getByRole("tab", { name: "Relationships" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Clusters" })).toHaveCount(0);

  // Insight tab shows metric tiles and the Graph findings card
  await expect(page.getByText("Graph findings", { exact: true })).toBeVisible();
  await expect(page.getByText("Entries in graph", { exact: true })).toBeVisible();
  // CTA buttons link to deeper tabs
  await expect(page.getByRole("button", { name: "Evidence graph →" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Similarity →" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Communities →" })).toBeVisible();
  // Shell-ready placeholder must not appear
  await expect(page.getByText("Relationships shell ready")).toHaveCount(0);

  // Navigate to Evidence tab and verify its live content
  await page.getByRole("tab", { name: "Evidence" }).click();
  await expect(page.getByRole("tab", { name: "Evidence" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await expect(page.getByText("Filters", { exact: true })).toBeVisible();
  await expect(page.getByText("Label search", { exact: true })).toBeVisible();
  await expect(page.getByText(/Showing \d+ nodes and \d+ edges/)).toBeVisible();

  // Navigate to Similarity tab and verify its live content
  await page.getByRole("tab", { name: "Similarity" }).click();
  await expect(page.getByText(/Selected (entry|point)/)).toBeVisible();
  const similaritySignal = page
    .getByText("Orientation map (secondary)", { exact: true })
    .or(page.getByText(/Showing \d+ \/ \d+ loaded points/))
    .or(page.getByRole("heading", { name: "No embedding points available" }));
  await expect(similaritySignal.first()).toBeVisible();
  await expect(page.getByText("Clusters shell ready")).toHaveCount(0);
});

test("graph communities tab renders and is not a placeholder shell", async ({ page }) => {
  await page.goto("/graph/");
  await expect(page.getByRole("heading", { level: 1, name: "Graph" })).toBeVisible({
    timeout: 20_000,
  });

  const communitiesTab = page.getByRole("tab", { name: "Communities" });
  await expect(communitiesTab).toBeVisible();
  await communitiesTab.click();
  await expect(communitiesTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("Communities shell ready")).toHaveCount(0);
});

test("graph deep-link aliases redirect to canonical tab hashes", async ({ page }) => {
  // Canonical #similarity deep link should stay selected directly
  await page.goto("/graph/#similarity");
  await expect(page.getByRole("heading", { level: 1, name: "Graph" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page).toHaveURL(/\/graph\/?#similarity$/);
  await expect(page.getByRole("tab", { name: "Similarity" })).toHaveAttribute(
    "aria-selected",
    "true"
  );

  // #relationships is a legacy alias for #evidence
  await page.goto("/graph/#relationships");
  await expect(page.getByRole("heading", { level: 1, name: "Graph" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page).toHaveURL(/\/graph\/?#evidence$/);
  await expect(page.getByRole("tab", { name: "Evidence" })).toHaveAttribute(
    "aria-selected",
    "true"
  );

  // #clusters is a legacy alias for #similarity
  await page.goto("/graph/#clusters");
  await expect(page).toHaveURL(/\/graph\/?#similarity$/);
  await expect(page.getByRole("tab", { name: "Similarity" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
});

test("insights search quality tab shows empty state when no data is available", async ({
  page,
}) => {
  // Intercept eval/stats to return an empty aggregation so the empty state is deterministic
  await page.route("**/api/eval/stats*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ aggregation: [], recent_comments: [] }),
    });
  });

  await page.goto("/insights/");
  await expect(page.getByRole("tab", { name: "Search Quality" })).toBeVisible({
    timeout: 20_000,
  });
  await page.getByRole("tab", { name: "Search Quality" }).click();
  await expect(page.getByRole("tab", { name: "Search Quality" })).toHaveAttribute(
    "aria-selected",
    "true"
  );

  // Empty state (not a headers-only table) must be visible
  await expect(page.getByText("No search evaluations yet")).toBeVisible({ timeout: 20_000 });
  // Table must not be rendered when there is no data
  await expect(page.getByRole("table")).toHaveCount(0);
});

test("insights workflow tab renders workflow health findings from API", async ({ page }) => {
  await page.route("**/api/workflow/health*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(WORKFLOW_HEALTH_FIXTURE),
    });
  });

  await page.goto("/insights/#workflow");
  await expect(page.getByRole("tab", { name: "Workflow" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await expect(page.getByText("Grade: B", { exact: true })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/Heavy sessions need summarization/)).toBeVisible();
  await expect(
    page.getByText("2 sessions crossed the event budget without fresh checkpoints.", {
      exact: true,
    })
  ).toBeVisible();
  await expect(
    page.getByText("Run distill on sessions that exceed the event budget.", { exact: true })
  ).toBeVisible();
});

test("insights workflow tab can reload from unavailable to findings", async ({ page }) => {
  let attempts = 0;
  await page.route("**/api/workflow/health*", async (route) => {
    attempts += 1;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(attempts <= 2 ? { invalid: true } : WORKFLOW_HEALTH_FIXTURE),
    });
  });

  await page.goto("/insights/#workflow");
  await expect(page.getByText("Workflow health unavailable", { exact: true })).toBeVisible({
    timeout: 20_000,
  });
  await page.getByRole("button", { name: "Reload" }).click();
  await expect(page.getByText(/Heavy sessions need summarization/)).toBeVisible({
    timeout: 20_000,
  });
});

test("insights retro tab renders session behavior metrics when provided", async ({ page }) => {
  await page.route("**/api/retro/summary*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(RETRO_BEHAVIOR_FIXTURE),
    });
  });

  await page.goto("/insights/#retro");
  await expect(page.getByRole("tab", { name: "Retro" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { level: 2, name: "Retrospective" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText(/Session Behavior/)).toBeVisible();
  await expect(page.getByText("Completion Rate", { exact: true })).toBeVisible();
  await expect(page.getByText("75.0%")).toBeVisible();
  await expect(page.getByText("Knowledge Yield", { exact: true })).toBeVisible();
  await expect(page.getByText("1.50 entries/session", { exact: true })).toBeVisible();
  await expect(page.getByText("One-Shot Rate", { exact: true })).toBeVisible();
  await expect(page.getByText("50.0%")).toBeVisible();
});

test("insights search quality tab renders Wave 2 diagnostics", async ({ page }) => {
  const now = new Date();
  const oneDayAgo = new Date(now);
  oneDayAgo.setDate(now.getDate() - 1);
  const threeDaysAgo = new Date(now);
  threeDaysAgo.setDate(now.getDate() - 3);

  await page.route("**/api/eval/stats*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        aggregation: [
          { query: "find bugs", up: 3, down: 1, neutral: 0, total: 4 },
          { query: "summarize sessions", up: 1, down: 0, neutral: 0, total: 1 },
        ],
        recent_comments: [
          {
            query: "find bugs",
            result_id: "r1",
            verdict: 1,
            comment: "Very helpful!",
            created_at: oneDayAgo.toISOString(),
          },
          {
            query: "summarize sessions",
            result_id: "r2",
            verdict: -1,
            comment: "Missed the key checkpoint.",
            created_at: threeDaysAgo.toISOString(),
          },
        ],
      }),
    });
  });
  await page.goto("/insights/#search-quality");
  await expect(page.getByRole("tab", { name: "Search Quality" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await expect(page.getByText("Approval rate distribution", { exact: true })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText("Embedding coverage", { exact: true })).toBeVisible();
  await expect(
    page.getByText("78% of knowledge entries have embeddings", { exact: true })
  ).toBeVisible();
  await expect(page.getByText("Feedback activity (14 days)", { exact: true })).toBeVisible();
  await expect(page.getByText("find bugs", { exact: true })).toBeVisible();
  await expect(page.getByText("Very helpful!", { exact: true })).toBeVisible();
});

test("insights tabs render first-class surfaces and retro loads repo-mode summary", async ({
  page,
}) => {
  await page.goto("/insights/");

  // Overview is the new default tab (no Dashboard tab)
  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("tab", { name: "Overview" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  // All five first-class tabs must be present
  await expect(page.getByRole("tab", { name: "Knowledge" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Retro" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Search Quality" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Live feed" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Workflow" })).toBeVisible();
  await expect(page.getByRole("tablist")).toHaveCSS("flex-direction", "column");
  // Old Dashboard tab must not exist
  await expect(page.getByRole("tab", { name: "Dashboard" })).toHaveCount(0);

  // Overview tab shows KPI tiles and CTA links to other tabs
  await expect(page.getByRole("button", { name: "Full Knowledge insights →" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Full Retrospective →" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Full Search Quality →" })).toBeVisible();
  await page.getByRole("button", { name: "Full Knowledge insights →" }).click();
  await expect(page.getByRole("tab", { name: "Knowledge" })).toHaveAttribute(
    "aria-selected",
    "true"
  );

  // Navigate to Retro tab and verify it loads repo-mode summary
  await page.getByRole("tab", { name: "Retro" }).click();
  await expect(page.getByRole("tab", { name: "Retro" })).toHaveAttribute("aria-selected", "true");
  // "Retrospective" heading renders inside the Retro tab
  await expect(page.getByRole("heading", { level: 2, name: "Retrospective" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText(/mode:\s*repo/i)).toBeVisible({ timeout: 20_000 });
});

test("settings page operator diagnostics cards render", async ({ page }) => {
  await page.goto("/settings/");
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible({
    timeout: 20_000,
  });

  // All five diagnostic card titles render unconditionally (regardless of API state).
  await expect(page.getByText("Sync diagnostics", { exact: true })).toBeVisible();
  await expect(page.getByText("Trend Scout diagnostics", { exact: true })).toBeVisible();
  await expect(page.getByText("Tentacle runtime diagnostics", { exact: true })).toBeVisible();
  await expect(page.getByText("Skill outcome metrics", { exact: true })).toBeVisible();
  await expect(page.getByText("System health", { exact: true })).toBeVisible();
});

test("settings page operator-actions panels are display-only", async ({ page }) => {
  await page.goto("/settings/");
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible({
    timeout: 20_000,
  });

  // Allow API calls to settle (data, error, or loading state is acceptable).
  await page.waitForLoadState("networkidle");

  // At least one operator-actions panel must render in the smoke environment.
  const operatorChecksLabel = page.getByText("Operator checks (read-only)");
  await expect(operatorChecksLabel.first()).toBeVisible({ timeout: 15_000 });

  // If operator panels are present, every action button inside them must be Copy-only.
  const panelCount = await operatorChecksLabel.count();
  expect(panelCount).toBeGreaterThan(0);
  for (let i = 0; i < panelCount; i += 1) {
    const buttons = operatorChecksLabel.nth(i).locator("..").getByRole("button");
    const buttonCount = await buttons.count();
    expect(buttonCount).toBeGreaterThan(0);
    for (let j = 0; j < buttonCount; j += 1) {
      await expect(buttons.nth(j)).toHaveAccessibleName("Copy");
    }
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Flight Recorder v3 — local Playwright proof (synthesis §4d/§4e).
//
// Acceptance:
//   * #timeline renders flight-recorder-header, chapter-rail, hotspot-drawer-toggle,
//     resume-drawer-toggle.
//   * Chapter rail uses data-chapter-mode="checkpoint" when the checkpoints API
//     returns ≥1 entry; chapter count equals the API total.
//   * task_complete ticks render once per session.task_complete / task_complete
//     skeleton entry mapped into a chapter span.
//   * Resume anchors render one row per snapshot returned by the rewind-snapshots
//     API; aria-expanded toggles on the drawer.
//   * Jump buttons (flight-recorder-jump-error, flight-recorder-jump-last-response)
//     seek without producing console.error / pageerror / API 4xx (enforced by
//     runtimeErrorGuard).
//   * #debug-log renders the flight-recorder-mission-strip alongside existing
//     Debug Flow v2 selectors (debug-log-flow-chart, debug-log-flow-trace).
//
// Hosted parity: hosted agents.linhngo.dev currently serves buildHash 70ab6f1
// (Debug Flow v2 only). The v3 selectors below are absent on hosted until the
// `flight-recorder-v3-ui` PR lands and Firebase redeploys; hosted proof is
// recorded as BLOCKED in the tentacle handoff rather than weakening the
// selectors here.
test("Flight Recorder v3: timeline+debug-log render header/rail/drawers/mission strip", async ({
  page,
}) => {
  await assertSeededSessionAvailable(page);
  await stubEmptyMissionAtlas(page);

  // ── Skeleton entries used by the Timeline projection ─────────────────────
  // 6 entries spanning turn_start → tool_call → task_complete → agent_response
  // → error → task_complete. Two task_completes prove the rail tick rendering
  // and resume anchor span_id matching.
  const FR_SKELETON_ENTRIES = [
    {
      idx: 0,
      timestamp: "2026-05-01T00:00:00.000Z",
      kind: "turn_start",
      duration_ms: 100,
      status: "ok",
      span_id: "span-turn-0",
      parent_span_id: null,
    },
    {
      idx: 1,
      timestamp: "2026-05-01T00:00:01.000Z",
      kind: "tool_call",
      duration_ms: 50,
      status: "ok",
      span_id: "span-tool-1",
      parent_span_id: "span-turn-0",
    },
    {
      idx: 2,
      timestamp: "2026-05-01T00:00:02.000Z",
      kind: "task_complete",
      duration_ms: 0,
      status: "ok",
      span_id: "span-tc-1",
      parent_span_id: "span-turn-0",
    },
    {
      idx: 3,
      timestamp: "2026-05-01T00:00:03.000Z",
      kind: "agent_response",
      duration_ms: 200,
      status: "ok",
      span_id: "span-resp-3",
      parent_span_id: "span-turn-0",
    },
    {
      idx: 4,
      timestamp: "2026-05-01T00:00:04.000Z",
      kind: "tool_call",
      duration_ms: 75,
      status: "error",
      span_id: "span-err-4",
      parent_span_id: "span-turn-0",
    },
    {
      idx: 5,
      timestamp: "2026-05-01T00:00:05.000Z",
      kind: "task_complete",
      duration_ms: 0,
      status: "ok",
      span_id: "span-tc-2",
      parent_span_id: "span-turn-0",
    },
  ];

  const FR_SKELETON_PAYLOAD = {
    schema_version: "debug-log/1",
    session_id: SEEDED_SESSION_ID,
    from: 0,
    limit: 5000,
    total: FR_SKELETON_ENTRIES.length,
    has_more: false,
    entries: FR_SKELETON_ENTRIES,
  };

  // Full payload (no projection) — must include `message`, `level`, `source`,
  // `tool_name`, and `attrs:null` so deriveMissionRollup populates tool chips.
  const FR_FULL_PAYLOAD = {
    schema_version: "debug-log/1",
    session_id: SEEDED_SESSION_ID,
    from: 0,
    limit: 50,
    total: FR_SKELETON_ENTRIES.length,
    has_more: false,
    entries: FR_SKELETON_ENTRIES.map((e) => ({
      ...e,
      level: e.status === "error" ? "error" : "info",
      source: e.kind === "tool_call" ? "tool" : "copilot-cli",
      // Stable, redaction-safe message text (no user prompts / file paths).
      message:
        e.kind === "tool_call"
          ? "Tool call completed"
          : e.kind === "task_complete"
            ? "Task complete marker"
            : e.kind === "agent_response"
              ? "Assistant produced response"
              : e.kind === "turn_start"
                ? "Turn started"
                : "event",
      tool_name: e.kind === "tool_call" ? "view" : null,
      attrs: null,
      redacted: false,
    })),
  };

  const FR_CHECKPOINTS = [
    {
      seq: 1,
      title: "Phase A",
      file_basename: "checkpoint_001.md",
      byte_size: 1024,
      mtime_iso: "2026-05-01T00:00:01.500Z",
      sections: {
        overview: true,
        history: false,
        work_done: true,
        technical_details: false,
        important_files: false,
        next_steps: true,
      },
    },
    {
      seq: 2,
      title: "Phase B",
      file_basename: "checkpoint_002.md",
      byte_size: 1024,
      mtime_iso: "2026-05-01T00:00:03.000Z",
      sections: {
        overview: true,
        history: true,
        work_done: true,
        technical_details: false,
        important_files: false,
        next_steps: false,
      },
    },
    {
      seq: 3,
      title: "Phase C",
      file_basename: "checkpoint_003.md",
      byte_size: 1024,
      mtime_iso: "2026-05-01T00:00:04.500Z",
      sections: {
        overview: true,
        history: false,
        work_done: true,
        technical_details: true,
        important_files: false,
        next_steps: true,
      },
    },
  ];

  const FR_CHECKPOINTS_PAYLOAD = {
    schema_version: "checkpoints/1",
    session_id: SEEDED_SESSION_ID,
    total: FR_CHECKPOINTS.length,
    checkpoints: FR_CHECKPOINTS,
  };

  const FR_REWIND_SNAPSHOTS = [
    {
      snapshot_id: "snap-a",
      timestamp: "2026-05-01T00:00:02.000Z",
      git_commit: "0000000000000000000000000000000000000001",
      git_branch: "main",
      file_count: 2,
      user_message_present: true,
      user_message_byte_size: 64,
      event_span_id: "span-tc-1",
    },
    {
      snapshot_id: "snap-b",
      timestamp: "2026-05-01T00:00:05.000Z",
      git_commit: "0000000000000000000000000000000000000002",
      git_branch: "main",
      file_count: 1,
      user_message_present: false,
      user_message_byte_size: 0,
      event_span_id: "span-tc-2",
    },
  ];

  const FR_REWIND_PAYLOAD = {
    schema_version: "rewind-snapshots/1",
    session_id: SEEDED_SESSION_ID,
    total: FR_REWIND_SNAPSHOTS.length,
    snapshots: FR_REWIND_SNAPSHOTS,
  };

  const FR_SUBAGENT_ACTIVITY_PAYLOAD = {
    schema_version: "1",
    session_id: SEEDED_SESSION_ID,
    total_subagents_seen: 3,
    returned: 3,
    cap: 1000,
    truncated: false,
    dropped_pending_starts: 0,
    entries: [
      {
        span_id: "subagent-code-review",
        agent_name: "code-review",
        agent_display_name: "Code Review",
        model: "claude-sonnet-4.6",
        status: "completed",
        started_at: "2026-05-01T00:00:01.000Z",
        ended_at: "2026-05-01T00:00:03.000Z",
        duration_ms: 2000,
        total_tool_calls: 4,
        total_tokens: 12000,
        error_category: null,
        error_preview: null,
        start_idx: 1,
        end_idx: 3,
        redacted: false,
      },
      {
        span_id: "subagent-security",
        agent_name: "browser-security-reviewer",
        agent_display_name: "Browser Security Reviewer",
        model: "claude-opus-4.6",
        status: "failed",
        started_at: "2026-05-01T00:00:04.000Z",
        ended_at: "2026-05-01T00:00:04.500Z",
        duration_ms: 500,
        total_tool_calls: 1,
        total_tokens: 4096,
        error_category: "rate_limited",
        error_preview: null,
        start_idx: 4,
        end_idx: 4,
        redacted: false,
      },
      {
        span_id: "subagent-research",
        agent_name: "research-planner",
        agent_display_name: "Research Planner",
        model: "claude-haiku-4.5",
        status: "running",
        started_at: "2026-05-01T00:00:05.000Z",
        ended_at: null,
        duration_ms: null,
        total_tool_calls: 0,
        total_tokens: null,
        error_category: null,
        error_preview: null,
        start_idx: 5,
        end_idx: null,
        redacted: false,
      },
    ],
  };

  const FR_SUBAGENT_INTERNALS_PAYLOAD = {
    schema_version: "1",
    session_id: SEEDED_SESSION_ID,
    total_agents_seen: 3,
    returned: 3,
    cap: 1000,
    truncated: false,
    dropped_pending_starts: 0,
    skill_correlation_supported: false,
    uncorrelated_skill_invocations: 2,
    session_skill_names: ["code-reviewer"],
    entries: [
      {
        agent_key_hash: "0123456789abcdef",
        span_id: "subagent-code-review",
        agent_name: "code-review",
        agent_display_name: "Code Review",
        model: "claude-sonnet-4.6",
        status: "completed",
        started_at: "2026-05-01T00:00:01.000Z",
        ended_at: "2026-05-01T00:00:03.000Z",
        duration_ms: 2000,
        start_idx: 1,
        end_idx: 3,
        redacted: false,
        internals: {
          internal_event_count: 4,
          tool_call_count: 2,
          tool_success_count: 1,
          tool_failure_count: 0,
          llm_turn_count: 1,
          output_tokens_total: 900,
          tool_names: ["view", "rg"],
          tools: [
            {
              idx: 2,
              end_idx: 3,
              tool_name: "view",
              status: "completed",
              started_at: "2026-05-01T00:00:01.500Z",
              ended_at: "2026-05-01T00:00:01.700Z",
              duration_ms: 200,
              input_bytes: 100,
              output_bytes: 2048,
            },
          ],
          tools_truncated: false,
          model_events: [
            {
              idx: 3,
              timestamp: "2026-05-01T00:00:02.000Z",
              output_tokens: 900,
              tool_request_count: 1,
            },
          ],
          model_events_truncated: false,
        },
      },
      {
        agent_key_hash: "1111111111111111",
        span_id: "subagent-security",
        agent_name: "browser-security-reviewer",
        agent_display_name: "Browser Security Reviewer",
        model: "claude-opus-4.6",
        status: "failed",
        started_at: "2026-05-01T00:00:04.000Z",
        ended_at: "2026-05-01T00:00:04.500Z",
        duration_ms: 500,
        start_idx: 4,
        end_idx: 4,
        redacted: false,
        internals: {
          internal_event_count: 1,
          tool_call_count: 1,
          tool_success_count: 0,
          tool_failure_count: 1,
          llm_turn_count: 0,
          output_tokens_total: 0,
          tool_names: ["research"],
          tools: [],
          tools_truncated: false,
          model_events: [],
          model_events_truncated: false,
        },
      },
      {
        agent_key_hash: "2222222222222222",
        span_id: "subagent-research",
        agent_name: "research-planner",
        agent_display_name: "Research Planner",
        model: "claude-haiku-4.5",
        status: "running",
        started_at: "2026-05-01T00:00:05.000Z",
        ended_at: null,
        duration_ms: null,
        start_idx: 5,
        end_idx: null,
        redacted: false,
        internals: {
          internal_event_count: 0,
          tool_call_count: 0,
          tool_success_count: 0,
          tool_failure_count: 0,
          llm_turn_count: 0,
          output_tokens_total: 0,
          tool_names: [],
          tools: [],
          tools_truncated: false,
          model_events: [],
          model_events_truncated: false,
        },
      },
    ],
  };

  // ── Route stubs ──────────────────────────────────────────────────────────
  await page.route(`**/api/session/${SEEDED_SESSION_ID}/debug-log*`, async (route) => {
    const url = new URL(route.request().url());
    const isSkeleton = url.searchParams.get("projection") === "skeleton";
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(isSkeleton ? FR_SKELETON_PAYLOAD : FR_FULL_PAYLOAD),
    });
  });
  await page.route(`**/api/session/${SEEDED_SESSION_ID}/checkpoints`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(FR_CHECKPOINTS_PAYLOAD),
    });
  });
  await page.route(`**/api/session/${SEEDED_SESSION_ID}/rewind-snapshots`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(FR_REWIND_PAYLOAD),
    });
  });
  await page.route(`**/api/session/${SEEDED_SESSION_ID}/subagent-activity`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(FR_SUBAGENT_ACTIVITY_PAYLOAD),
    });
  });
  await page.route(`**/api/session/${SEEDED_SESSION_ID}/subagent-internals`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(FR_SUBAGENT_INTERNALS_PAYLOAD),
    });
  });

  // ── #timeline assertions ────────────────────────────────────────────────
  await page.goto(`/sessions/${SEEDED_SESSION_ID}/#timeline`);
  await expect(page.getByRole("tab", { name: "Timeline" })).toHaveAttribute(
    "aria-selected",
    "true",
    { timeout: 20_000 }
  );

  const header = page.getByTestId("flight-recorder-header");
  await expect(header).toBeVisible({ timeout: 20_000 });
  // Header shows ERROR (1) because one skeleton entry has status="error".
  await expect(page.getByTestId("flight-recorder-status")).toContainText(/ERROR/);

  const subagentPanel = page.getByTestId("subagent-activity-panel");
  await expect(subagentPanel).toBeVisible();
  await expect(page.getByTestId("subagent-summary-total")).toHaveText("3 runs");
  await expect(page.getByTestId("subagent-summary-failed")).toHaveText("1 failed");
  await expect(page.getByTestId("subagent-summary-running")).toHaveText("1 running");
  await expect(page.getByTestId("subagent-activity-row-subagent-code-review")).toBeVisible();
  await expect(page.getByTestId("subagent-activity-row-subagent-security-outcome")).toHaveText(
    "Failed: rate_limited"
  );
  await page.getByTestId("subagent-activity-row-subagent-code-review").click();
  await expect(page.getByTestId("subagent-activity-row-subagent-code-review-trace")).toContainText(
    "2 tools"
  );
  await expect(
    page.getByTestId("subagent-activity-row-subagent-code-review-trace-tools")
  ).toContainText("view");
  await expect(
    page.getByTestId("subagent-activity-row-subagent-code-review-trace-skills")
  ).toContainText("session-level skill load");
  await page.getByTestId("subagent-filter-failed").click();
  await expect(page.getByTestId("subagent-activity-row-subagent-security")).toBeVisible();
  await expect(page.getByTestId("subagent-activity-row-subagent-code-review")).toBeHidden();
  await page.getByTestId("subagent-filter-all").click();

  const rail = page.getByTestId("chapter-rail");
  await expect(rail).toBeVisible();

  // Checkpoint mode: one chapter per checkpoint, all data-chapter-mode="checkpoint".
  const chapterButtons = page.locator(
    '[data-testid^="chapter-rail-chapter-"][data-chapter-mode="checkpoint"]'
  );
  await expect(chapterButtons).toHaveCount(FR_CHECKPOINTS.length);
  for (const cp of FR_CHECKPOINTS) {
    await expect(
      page.locator(`[data-testid="chapter-rail-chapter-${cp.seq}"][data-chapter-mode="checkpoint"]`)
    ).toHaveCount(1);
  }

  // task_complete ticks: one per task_complete skeleton entry.
  const expectedTaskCompletes = FR_SKELETON_ENTRIES.filter(
    (e) => e.kind === "task_complete" || e.kind === "session.task_complete"
  ).length;
  await expect(page.locator('[data-testid^="chapter-rail-task-complete-"]')).toHaveCount(
    expectedTaskCompletes
  );

  // Hotspot drawer: toggle expands.
  const hotspotToggle = page.getByTestId("hotspot-drawer-toggle");
  await expect(hotspotToggle).toBeVisible();
  await expect(hotspotToggle).toHaveAttribute("aria-expanded", "false");
  await hotspotToggle.click();
  await expect(hotspotToggle).toHaveAttribute("aria-expanded", "true");

  // Resume drawer: toggle expands, anchors match API total.
  const resumeToggle = page.getByTestId("resume-drawer-toggle");
  await expect(resumeToggle).toBeVisible();
  await expect(resumeToggle).toHaveAttribute("aria-expanded", "false");
  await resumeToggle.click();
  await expect(resumeToggle).toHaveAttribute("aria-expanded", "true");
  const anchorRows = page.locator('[data-testid^="resume-anchor-"]');
  await expect(anchorRows).toHaveCount(FR_REWIND_SNAPSHOTS.length);
  for (const snap of FR_REWIND_SNAPSHOTS) {
    await expect(page.getByTestId(`resume-anchor-${snap.snapshot_id}`)).toBeVisible();
  }

  // Jump buttons must not produce runtime errors (runtimeErrorGuard enforces).
  const jumpError = page.getByTestId("flight-recorder-jump-error");
  await expect(jumpError).toBeVisible();
  await jumpError.click();
  const jumpLast = page.getByTestId("flight-recorder-jump-last-response");
  await expect(jumpLast).toBeVisible();
  await jumpLast.click();

  // ── #debug-log assertions ────────────────────────────────────────────────
  await page.goto(`/sessions/${SEEDED_SESSION_ID}/#debug-log`);
  await expect(page.getByRole("tab", { name: "Debug Log" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("debug-log-view-flow")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("debug-log-view-flow").click();

  // Flight Recorder v3 mission strip + chips (Tools/Hooks/Skills/Sub-agents/
  // Compactions/Errors).  The strip is rendered inside the flow view.
  const missionStrip = page.getByTestId("flight-recorder-mission-strip");
  await expect(missionStrip).toBeVisible();
  await expect(page.getByTestId("mission-chip-tools")).toBeVisible();
  await expect(page.getByTestId("mission-chip-hooks")).toBeVisible();
  await expect(page.getByTestId("mission-chip-skills")).toBeVisible();
  await expect(page.getByTestId("mission-chip-subagents")).toHaveText("Sub-agents: 3");
  await expect(page.getByTestId("mission-chip-compactions")).toBeVisible();
  await expect(page.getByTestId("mission-chip-errors")).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-subagent-internals")).toContainText("3 agents");

  const debugSubagentPanel = page.getByTestId("subagent-activity-panel");
  await expect(debugSubagentPanel).toBeVisible();
  await page.getByTestId("subagent-filter-failed").click();
  await expect(page.getByTestId("subagent-filter-failed")).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("mission-chip-subagents").click();
  await expect(page.getByTestId("subagent-filter-all")).toHaveAttribute("aria-pressed", "true");
  await expect(debugSubagentPanel).toHaveAttribute("data-flash", "true");
  await page.getByTestId("subagent-activity-row-subagent-code-review-jump").click();
  await expect(page.getByRole("dialog", { name: "Debug event detail" })).toBeVisible();
  await expect(page.locator("#detail-1")).toContainText("idx");
  await expect(page.locator("#detail-1")).toContainText("1");

  // Existing Debug Flow v2 selectors must still render.
  await expect(page.getByTestId("debug-log-flow-chart")).toBeVisible();
  await expect(page.getByTestId("debug-log-flow-trace")).toBeVisible();
});

test("search feedback submits and resets when the query changes", async ({ page }) => {
  await page.goto("/search/?q=deterministic");
  const searchInput = page.getByRole("searchbox", { name: "Search sessions and knowledge" });
  const helpfulButton = page.getByRole("button", { name: "Helpful result" }).first();

  await expect(helpfulButton).toBeVisible({ timeout: 20_000 });
  await helpfulButton.click();
  await expect(page.getByText("👍 Thanks!").first()).toBeVisible();

  await searchInput.fill("e2e");
  await expect(searchInput).toHaveValue("e2e");
  await searchInput.press("Enter");
  await expect(page.getByRole("button", { name: "Helpful result" }).first()).toBeVisible({
    timeout: 20_000,
  });
});
