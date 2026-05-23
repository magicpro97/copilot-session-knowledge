/**
 * hosted-regression.spec.ts — Hosted UI regression proof harness (#519).
 *
 * Gated by `HOSTED_PROOF=1`. Default CI must NOT run this project unless
 * that env var is set.
 *
 * Required environment variables:
 *   HOSTED_PROOF=1          — enables this project in playwright.config.ts
 *   HOSTED_URL              — (optional) defaults to https://agents.linhngo.dev
 *   CLI_SESSION_ID          — a real CLI session ID to probe; required for
 *                             session-detail assertions
 *   NO_LOCAL_BACKEND=1      — (optional) skip assertions that require a running
 *                             local browse backend on 127.0.0.1:8765
 *
 * Running (with real session):
 *   cd browse-ui
 *   pnpm exec playwright install chromium
 *   HOSTED_PROOF=1 \
 *     HOSTED_URL=https://agents.linhngo.dev \
 *     CLI_SESSION_ID=<real-session-id> \
 *     pnpm exec playwright test --project hosted-regression --reporter=line,html --trace on
 *
 * Dry-run / list (no real session needed):
 *   HOSTED_PROOF=1 HOSTED_URL=https://agents.linhngo.dev CLI_SESSION_ID=dummy \
 *     pnpm exec playwright test --project hosted-regression --list
 *
 * Artifacts written per-test to the Playwright output directory:
 *   loopback-hits.json        — HTTP requests from hosted frame to 127.0.0.1:8765/localhost:8765
 *   runs-404.json             — 404 responses for /api/operator/sessions/<id>/runs
 *   app-console-errors.json   — app-owned console errors and warnings
 *   extension-noise.json      — browser extension console messages (evidence only, not failures)
 *   browser-internal.json     — browser-internal messages (evidence only, not failures)
 *   console-raw.json          — all console messages unfiltered
 *
 * IMPORTANT: This spec does NOT use the global runtimeErrorGuard or the runs-stub
 * from fixtures.ts. It is isolated in hosted-fixtures.ts to ensure no stubs mask
 * real 404s or loopback requests from the hosted origin.
 *
 * Pass criteria (final, when fixes land in #516):
 *   loopback-hits.json  → []
 *   runs-404.json       → []
 *   app-console-errors.json → []
 *
 * Baseline PR pass criteria (this PR — #519 harness):
 *   All three artifact files are valid JSON arrays. Contents are recorded as
 *   diagnostic evidence. Non-empty arrays are expected until #516 fixes land;
 *   this PR does NOT assert empty (see HOSTED_PROOF_STRICT below).
 */

// NOTE: This file is only collected by the `hosted-regression` Playwright project,
// which is only registered when HOSTED_PROOF=1. The import below is safe in all
// non-hosted contexts because this spec is never loaded by default.
import type { Page } from "@playwright/test";
import { test, expect, HOSTED_URL, CLI_SESSION_ID } from "./hosted-fixtures";

// ── Env flags ─────────────────────────────────────────────────────────────────

/**
 * HOSTED_PROOF_STRICT=1 enables assertions that require an empty artifact list.
 * Leave unset on the baseline harness PR; set when final fixes in #516 have landed.
 */
const HOSTED_PROOF_STRICT = Boolean(process.env.HOSTED_PROOF_STRICT);

/** NO_LOCAL_BACKEND=1 disables assertions/behaviours that require the local backend. */
const NO_LOCAL_BACKEND = Boolean(process.env.NO_LOCAL_BACKEND);

const HOSTED_ORIGIN = new URL(HOSTED_URL).origin;


/**
 * Grant Chrome Local Network Access (LNA) for the hosted origin only.
 * Chromium M123+ requires this Web permission before an HTTPS page may
 * probe loopback, even when the backend sends PNA opt-in headers.
 */
async function grantChromeLocalNetworkAccess(page: Page): Promise<void> {
  await page.context().grantPermissions(["local-network-access"], { origin: HOSTED_ORIGIN });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Navigate to the hosted app and leave a short settle window after DOM content loads.
 * The harness records hosted network/console evidence even when auth or backend
 * availability leaves the app in a loading or empty state.
 */
async function navigateHosted(page: Page, urlPath: string): Promise<void> {
  await grantChromeLocalNetworkAccess(page);
  const fullUrl = new URL(urlPath, HOSTED_URL).toString();
  await page.goto(fullUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
  // Wait for hydration and initial data fetches to settle.
  await page.waitForTimeout(3_000);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

/**
 * HOSTED_PROOF: Navigate to the hosted sessions list and collect evidence.
 *
 * Assertion: all artifact files are arrays (baseline proof).
 * When HOSTED_PROOF_STRICT=1: also asserts all arrays are empty.
 */
test("HOSTED_PROOF: sessions list — collect artifacts", async ({ page, hostedArtifacts }) => {
  await navigateHosted(page, "/sessions");

  // Baseline: all artifacts are arrays (proves harness ran without crashing).
  expect(Array.isArray(hostedArtifacts.loopbackHits)).toBe(true);
  expect(Array.isArray(hostedArtifacts.runs404)).toBe(true);
  expect(Array.isArray(hostedArtifacts.appConsoleErrors)).toBe(true);
  expect(Array.isArray(hostedArtifacts.extensionNoise)).toBe(true);
  expect(Array.isArray(hostedArtifacts.browserInternal)).toBe(true);
  expect(Array.isArray(hostedArtifacts.consoleRaw)).toBe(true);

  // Strict mode: assert fix has landed (only enable when #516 is merged).
  if (HOSTED_PROOF_STRICT) {
    expect(
      hostedArtifacts.loopbackHits,
      "loopback-hits must be empty when hosted UI no longer probes loopback"
    ).toHaveLength(0);
    expect(
      hostedArtifacts.appConsoleErrors,
      "app-console-errors must be empty when hosted UI is healthy"
    ).toHaveLength(0);
    expect(
      hostedArtifacts.runs404,
      "runs-404 must be empty when hosted operator-runs noise is fixed"
    ).toHaveLength(0);
  }
});

/**
 * HOSTED_PROOF: Navigate to the session detail debug-log tab with the given
 * CLI_SESSION_ID and capture the /runs 404 evidence.
 *
 * Skips gracefully when CLI_SESSION_ID is not set (dry-run / list mode).
 */
test("HOSTED_PROOF: session detail debug-log — capture runs-404", async ({
  page,
  hostedArtifacts,
}) => {
  test.skip(
    !CLI_SESSION_ID || CLI_SESSION_ID === "dummy",
    "CLI_SESSION_ID must be set to a real session ID to run this test"
  );

  // Navigate to session detail; attempt debug-log tab via hash fragment.
  // The app may or may not honour the hash on initial load — we navigate
  // first to the session list to let auth/HostProvider initialise, then
  // go to the session detail.
  await navigateHosted(page, "/sessions");
  await page.waitForTimeout(1_000);
  await navigateHosted(page, `/sessions/${CLI_SESSION_ID}#debug-log`);

  // Give the app time to render session detail and trigger the /runs fetch.
  await page.waitForTimeout(5_000);

  // Baseline: artifact arrays are well-formed.
  expect(Array.isArray(hostedArtifacts.runs404)).toBe(true);
  expect(Array.isArray(hostedArtifacts.loopbackHits)).toBe(true);

  // Strict mode: /runs must return 200 (fix landed).
  if (HOSTED_PROOF_STRICT) {
    expect(
      hostedArtifacts.runs404,
      "runs-404 must be empty — /api/operator/sessions/<id>/runs must return 200"
    ).toHaveLength(0);
    expect(
      hostedArtifacts.loopbackHits,
      "loopback-hits must be empty — hosted UI must not probe 127.0.0.1:8765"
    ).toHaveLength(0);
  }
});

/**
 * HOSTED_PROOF: No-backend mode — verify hosted UI does not make unguarded
 * loopback requests even when no local backend is running.
 *
 * Only meaningful when NO_LOCAL_BACKEND=1 is set by the caller to confirm
 * the backend is intentionally absent.
 */
test("HOSTED_PROOF: no-local-backend — hosted UI must not make loopback requests", async ({
  page,
  hostedArtifacts,
}) => {
  test.skip(!NO_LOCAL_BACKEND, "Set NO_LOCAL_BACKEND=1 to run this test without a local backend");

  await navigateHosted(page, "/sessions");
  await page.waitForTimeout(4_000);

  // In no-backend mode the expected behaviour once #516 is fixed is zero
  // loopback requests. Until then, this test records evidence only.
  expect(Array.isArray(hostedArtifacts.loopbackHits)).toBe(true);

  if (HOSTED_PROOF_STRICT) {
    expect(
      hostedArtifacts.loopbackHits,
      "hosted UI must not probe loopback when no local backend is present"
    ).toHaveLength(0);
  }
});
