import { expect, test } from "./fixtures";
import {
  aliasPlaceholderSession,
  assertSeededSessionAvailable,
  SEEDED_SESSION_ID,
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

test("shipped /v2 routes render expected headings", async ({ page }) => {
  const headingByRoute = [
    ["/v2/sessions/", "Sessions"],
    ["/v2/search/", "Search"],
    ["/v2/insights/", "Insights"],
    ["/v2/graph/", "Graph"],
    ["/v2/settings/", "Settings"],
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

  await page.goto("/v2/sessions/");
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
  await page.goto("/v2/sessions/_placeholder/");
  await expect(page).toHaveURL(/\/v2\/sessions\/_placeholder\/?(#overview)?$/);

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
  const placeholderSessionRequests: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes("/api/sessions/_placeholder") || url.includes("/api/session/_placeholder/")) {
      placeholderSessionRequests.push(url);
    }
  });

  await page.goto(`/v2/sessions/${SEEDED_SESSION_ID}/`);
  await expect(page).toHaveURL(new RegExp(`/v2/sessions/${SEEDED_SESSION_ID}/?(#overview)?$`));
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
});

test("sessions list click-through opens real UUID session detail", async ({ page }) => {
  await assertSeededSessionAvailable(page);
  await page.goto("/v2/sessions/");
  await expect(page.getByRole("heading", { level: 1, name: "Sessions" })).toBeVisible({
    timeout: 20_000,
  });

  await expect(page.locator("tbody tr div.animate-pulse")).toHaveCount(0);
  const firstDataRow = page.locator("tbody tr").first();
  await expect(firstDataRow).toBeVisible();
  await firstDataRow.click();
  await expect(page).toHaveURL(/\/v2\/sessions\/[^/]+\/?(#overview)?$/);
  await expect(page).not.toHaveURL(/\/v2\/sessions\/_placeholder\/?(#overview)?$/);
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

  await page.goto(`/v2/sessions/${SEEDED_SESSION_ID}/#checkpoints`);
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
  await page.goto("/v2/graph/");
  await expect(page.getByRole("heading", { level: 1, name: "Graph" })).toBeVisible({
    timeout: 20_000,
  });

  // New default: Insight tab is active
  await expect(page.getByRole("tab", { name: "Insight" })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/\/v2\/graph\/?#insight$/);

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
  await page.goto("/v2/graph/");
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
  await page.goto("/v2/graph/#similarity");
  await expect(page.getByRole("heading", { level: 1, name: "Graph" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page).toHaveURL(/\/v2\/graph\/?#similarity$/);
  await expect(page.getByRole("tab", { name: "Similarity" })).toHaveAttribute(
    "aria-selected",
    "true"
  );

  // #relationships is a legacy alias for #evidence
  await page.goto("/v2/graph/#relationships");
  await expect(page.getByRole("heading", { level: 1, name: "Graph" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page).toHaveURL(/\/v2\/graph\/?#evidence$/);
  await expect(page.getByRole("tab", { name: "Evidence" })).toHaveAttribute(
    "aria-selected",
    "true"
  );

  // #clusters is a legacy alias for #similarity
  await page.goto("/v2/graph/#clusters");
  await expect(page).toHaveURL(/\/v2\/graph\/?#similarity$/);
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

  await page.goto("/v2/insights/");
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

  await page.goto("/v2/insights/#workflow");
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

test("insights retro tab renders session behavior metrics when provided", async ({ page }) => {
  await page.route("**/api/retro/summary*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(RETRO_BEHAVIOR_FIXTURE),
    });
  });

  await page.goto("/v2/insights/#retro");
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
  await page.route("**/api/knowledge/insights*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(KNOWLEDGE_INSIGHTS_FIXTURE),
    });
  });

  await page.goto("/v2/insights/#search-quality");
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
  await page.goto("/v2/insights/");

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
  await page.goto("/v2/settings/");
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
  await page.goto("/v2/settings/");
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

test("search feedback submits and resets when the query changes", async ({ page }) => {
  await page.goto("/v2/search/?q=deterministic");
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
