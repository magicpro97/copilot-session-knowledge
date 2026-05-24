import type { Page, Route } from "@playwright/test";

export const SEEDED_SESSION_ID = "e2e-session-0001-abcdef";

export async function stubEmptyFlightRecorderRoutes(
  page: Page,
  sessionId = SEEDED_SESSION_ID
): Promise<void> {
  await page.route(`**/api/session/${sessionId}/debug-log*`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "debug-log/1",
        session_id: sessionId,
        from: 0,
        limit: 5000,
        total: 0,
        has_more: false,
        entries: [],
      }),
    });
  });

  await page.route(`**/api/session/${sessionId}/checkpoints`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "checkpoints/1",
        session_id: sessionId,
        total: 0,
        checkpoints: [],
      }),
    });
  });

  await page.route(`**/api/session/${sessionId}/rewind-snapshots`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "rewind-snapshots/1",
        session_id: sessionId,
        total: 0,
        snapshots: [],
      }),
    });
  });

  await page.route(`**/api/session/${sessionId}/subagent-activity`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "1",
        session_id: sessionId,
        total_subagents_seen: 0,
        returned: 0,
        cap: 1000,
        truncated: false,
        dropped_pending_starts: 0,
        entries: [],
      }),
    });
  });

  await page.route(`**/api/session/${sessionId}/subagent-internals`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "1",
        session_id: sessionId,
        total_agents_seen: 0,
        returned: 0,
        cap: 1000,
        truncated: false,
        dropped_pending_starts: 0,
        skill_correlation_supported: false,
        uncorrelated_skill_invocations: 0,
        session_skill_names: [],
        entries: [],
      }),
    });
  });
}

async function proxyToSessionId(route: Route, sessionId: string): Promise<void> {
  const requestUrl = new URL(route.request().url());
  requestUrl.pathname = requestUrl.pathname.replace(
    "/_placeholder",
    `/${encodeURIComponent(sessionId)}`
  );
  const response = await route.fetch({ url: requestUrl.toString() });
  await route.fulfill({ response });
}

export async function aliasPlaceholderSession(page: Page): Promise<string> {
  await page.route("**/api/sessions/_placeholder*", (route) =>
    proxyToSessionId(route, SEEDED_SESSION_ID)
  );
  await page.route("**/api/session/_placeholder/**", (route) =>
    proxyToSessionId(route, SEEDED_SESSION_ID)
  );
  await stubEmptyFlightRecorderRoutes(page, "_placeholder");
  return SEEDED_SESSION_ID;
}

export async function assertSeededSessionAvailable(page: Page): Promise<void> {
  const response = await page.request.get(`/api/sessions/${encodeURIComponent(SEEDED_SESSION_ID)}`);
  if (!response.ok()) {
    throw new Error(`Missing seeded session fixture: ${SEEDED_SESSION_ID} (${response.status()})`);
  }
}
