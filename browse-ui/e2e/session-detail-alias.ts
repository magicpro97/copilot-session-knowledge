import type { Page, Route } from "@playwright/test";

export const SEEDED_SESSION_ID = "e2e-session-0001-abcdef";

async function proxyToSessionId(route: Route, sessionId: string): Promise<void> {
  const requestUrl = new URL(route.request().url());
  requestUrl.pathname = requestUrl.pathname.replace("/_placeholder", `/${encodeURIComponent(sessionId)}`);
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
  return SEEDED_SESSION_ID;
}

export async function assertSeededSessionAvailable(page: Page): Promise<void> {
  const response = await page.request.get(`/api/sessions/${encodeURIComponent(SEEDED_SESSION_ID)}`);
  if (!response.ok()) {
    throw new Error(`Missing seeded session fixture: ${SEEDED_SESSION_ID} (${response.status()})`);
  }
}
