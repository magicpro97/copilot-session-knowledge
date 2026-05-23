// Hosted-UI regression proof (#519)
//
// Navigates to the real https://agents.linhngo.dev and collects three JSON
// evidence artifacts that document the current hosted-origin network behavior:
//
//   loopback-hits.json       - disallowed HTTP loopback requests/PNA-blocked
//                              attempts from the hosted page. HTTPS loopback
//                              probes are allowed candidates under #517.
//   runs-404.json            - /api/operator/sessions/{id}/runs responses with
//                              HTTP 404 (the real hosted-backend gap).
//   app-console-errors.json  - app-owned console errors after filtering
//                              browser-extension noise.
//
// Acceptance criteria (green state after #517/#518/#520 are merged):
//   loopback-hits.json        = []
//   runs-404.json             = []
//   app-console-errors.json   = []
//
// IMPORTANT - why this file does NOT import from ./fixtures:
//   fixtures.ts installs an auto-fixture that globally stubs every
//   /api/operator/sessions/{id}/runs request to HTTP 200.  That would mask
//   the real hosted 404 that this proof must detect.  By importing directly
//   from @playwright/test we see real network traffic.
//
// Gate: HOSTED_PROOF=1  (all tests skip without it)
//
// Run:
//   HOSTED_PROOF=1 pnpm exec playwright test hosted-proof --project hosted-proof
//   # or via the package script:
//   pnpm test:e2e:hosted

import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/** Hosted origin — always use absolute URLs in this spec to bypass baseURL. */
const HOSTED_ORIGIN = "https://agents.linhngo.dev";

/**
 * CLI session id verified to exist on the local backend.
 * The session detail route triggers the /api/operator/sessions/{id}/runs fetch
 * that is currently returning 404 on the hosted backend.
 * Override via CLI_SESSION_ID env var if a fresher session is available.
 */
const CLI_SESSION_ID = process.env.CLI_SESSION_ID ?? "33169957-0dc1-4998-86c0-d2beba02e8b4";

/** Absolute session-detail URL on the hosted origin. */
const SESSION_DETAIL_URL = `${HOSTED_ORIGIN}/sessions/${encodeURIComponent(CLI_SESSION_ID)}`;
const SESSION_DETAIL_DEBUG_LOG_URL = (() => {
  const url = new URL(SESSION_DETAIL_URL);
  url.hash = "debug-log";
  return url.toString();
})();
/** Explicit backend used by the hosted shell; default is an allowed HTTPS loopback candidate. */
const HOSTED_PROOF_BACKEND_URL = process.env.HOSTED_PROOF_BACKEND_URL ?? "https://127.0.0.1:8765";

// ---------------------------------------------------------------------------
// Noise filtering
// ---------------------------------------------------------------------------

/**
 * Patterns for clearly non-app-owned console messages.
 * Only filter items that are definitively browser-extension or browser-internal
 * noise; never filter messages that could originate from app code.
 */
const EXTENSION_NOISE_PATTERNS: RegExp[] = [
  /chrome-extension:\/\//,
  /lockdown-install\.js/,
  /onboarding\.js/,
  /\bcontent\.js\b/,
  /Unchecked runtime\.lastError/,
];

function isExtensionNoise(text: string): boolean {
  return EXTENSION_NOISE_PATTERNS.some((p) => p.test(text));
}

function isDisallowedHttpLoopbackUrl(url: string): boolean {
  return /^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?/.test(url);
}

function isRunsApiUrl(url: string): boolean {
  return /\/api\/operator\/sessions\/[^/]+\/runs/.test(url);
}

// ---------------------------------------------------------------------------
// Artifact types
// ---------------------------------------------------------------------------

interface LoopbackHit {
  url: string;
  method: string;
  status?: number;
  blocked?: boolean;
  error?: string;
  timestamp: string;
}

interface Runs404Entry {
  url: string;
  method: string;
  status: number;
  timestamp: string;
}

interface AppConsoleError {
  kind: "console" | "pageerror";
  message: string;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Hosted-UI regression proof", () => {
  // Gate: skip all tests in this file unless HOSTED_PROOF=1.
  // This ensures normal `pnpm test:e2e` runs are not slowed or broken.
  test.skip(
    process.env.HOSTED_PROOF !== "1",
    "Hosted proof requires HOSTED_PROOF=1. " +
      "Run: HOSTED_PROOF=1 pnpm exec playwright test hosted-proof --project hosted-proof"
  );

  test("HOSTED-PROOF-1: collect loopback-hits, runs-404, app-console-errors from agents.linhngo.dev", async ({
    page,
  }, testInfo) => {
    // Deduplicate loopback requests by method+url key
    const loopbackMap = new Map<string, LoopbackHit>();
    const runs404: Runs404Entry[] = [];
    const appConsoleErrors: AppConsoleError[] = [];
    const ts = () => new Date().toISOString();

    // -----------------------------------------------------------------------
    // Collectors
    // NOTE: No page.route() stubs are installed here.  We deliberately
    // observe all real network traffic to detect the /runs 404.
    // -----------------------------------------------------------------------

    // Track only disallowed HTTP loopback requests; hosted HTTPS probes are
    // documented #517 candidates and should not fail this artifact by themselves.
    page.on("request", (request) => {
      const url = request.url();
      if (!isDisallowedHttpLoopbackUrl(url)) return;
      const key = `${request.method()}:${url}`;
      if (!loopbackMap.has(key)) {
        loopbackMap.set(key, {
          url,
          method: request.method(),
          timestamp: ts(),
        });
      }
    });

    // Mark disallowed HTTP loopback requests as blocked when PNA/connection errors fire.
    page.on("requestfailed", (request) => {
      const url = request.url();
      if (!isDisallowedHttpLoopbackUrl(url)) return;
      const key = `${request.method()}:${url}`;
      const failure = request.failure();
      const existing = loopbackMap.get(key);
      if (existing) {
        existing.blocked = true;
        if (failure) existing.error = failure.errorText;
      } else {
        loopbackMap.set(key, {
          url,
          method: request.method(),
          blocked: true,
          error: failure?.errorText ?? undefined,
          timestamp: ts(),
        });
      }
    });

    // Record /runs 404 responses and disallowed HTTP loopback response status codes.
    page.on("response", (response) => {
      const url = response.url();
      const status = response.status();

      if (isRunsApiUrl(url) && status === 404) {
        runs404.push({
          url,
          method: response.request().method(),
          status,
          timestamp: ts(),
        });
      }

      if (isDisallowedHttpLoopbackUrl(url)) {
        const key = `${response.request().method()}:${url}`;
        const entry = loopbackMap.get(key);
        if (entry && entry.status === undefined) {
          entry.status = status;
        }
      }
    });

    // App-owned console errors (extension noise filtered)
    page.on("console", (message) => {
      if (message.type() !== "error") return;
      const text = message.text();
      if (isExtensionNoise(text)) return;
      appConsoleErrors.push({ kind: "console", message: text, timestamp: ts() });
    });

    // Uncaught page errors
    page.on("pageerror", (error) => {
      const text = error.stack ?? error.message;
      if (isExtensionNoise(text)) return;
      appConsoleErrors.push({ kind: "pageerror", message: text, timestamp: ts() });
    });

    // -----------------------------------------------------------------------
    // Navigation
    // -----------------------------------------------------------------------

    await page.addInitScript((baseUrl) => {
      const profile = {
        id: "hosted-proof-backend",
        label: "Hosted proof backend",
        base_url: baseUrl,
        token: "",
        cli_kind: "copilot",
        is_default: true,
      };
      window.localStorage.setItem("browse_host_profiles", JSON.stringify([profile]));
      window.localStorage.setItem("browse_selected_host_id", profile.id);
    }, HOSTED_PROOF_BACKEND_URL);

    // Step 1: Load hosted home page — exercises app shell init on the real hosted origin.
    await page.goto(HOSTED_ORIGIN, { waitUntil: "networkidle", timeout: 30_000 });
    // Brief pause to let deferred probes fire
    await page.waitForTimeout(2_000);

    // Step 2: Navigate directly to the Debug Log tab. The /runs request is gated
    // by activeTab === "debug-log", so the hash is required for this proof to
    // observe the old /api/operator/sessions/{id}/runs 404.
    await page.goto(SESSION_DETAIL_DEBUG_LOG_URL, { waitUntil: "networkidle", timeout: 30_000 });
    await expect(page.getByRole("tab", { name: "Debug Log" })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    await page.waitForLoadState("networkidle");
    // Allow time for polling / retries that the app may attempt
    await page.waitForTimeout(3_000);

    // -----------------------------------------------------------------------
    // Serialize and attach artifacts
    // -----------------------------------------------------------------------

    const loopbackHits: LoopbackHit[] = Array.from(loopbackMap.values());
    const loopbackJson = JSON.stringify(loopbackHits, null, 2);
    const runs404Json = JSON.stringify(runs404, null, 2);
    const consoleErrorsJson = JSON.stringify(appConsoleErrors, null, 2);

    // Write to test output directory for operator inspection outside the report
    writeFileSync(testInfo.outputPath("loopback-hits.json"), loopbackJson);
    writeFileSync(testInfo.outputPath("runs-404.json"), runs404Json);
    writeFileSync(testInfo.outputPath("app-console-errors.json"), consoleErrorsJson);

    // Attach to Playwright HTML report
    await testInfo.attach("loopback-hits.json", {
      contentType: "application/json",
      body: loopbackJson,
    });
    await testInfo.attach("runs-404.json", {
      contentType: "application/json",
      body: runs404Json,
    });
    await testInfo.attach("app-console-errors.json", {
      contentType: "application/json",
      body: consoleErrorsJson,
    });

    // Summary for CI stdout
    console.log(`[HOSTED-PROOF-1] loopback-hits: ${loopbackHits.length}`);
    console.log(`[HOSTED-PROOF-1] runs-404: ${runs404.length}`);
    console.log(`[HOSTED-PROOF-1] app-console-errors: ${appConsoleErrors.length}`);
    if (loopbackHits.length > 0) {
      console.log(`[HOSTED-PROOF-1] loopback detail:\n${loopbackJson}`);
    }

    // -----------------------------------------------------------------------
    // Assertions — expected to be RED until #517/#518/#520 are merged.
    // Do NOT soften these assertions; RED is the correct current state.
    // -----------------------------------------------------------------------

    // /runs 404: must be [] after #518 prevents non-operator sessions from
    // calling the operator-runs endpoint when the Debug Log tab is active.
    expect(
      runs404,
      [
        `runs-404.json must be [] (tracked in #518).`,
        `Found ${runs404.length} 404 response(s) on /api/operator/sessions/*/runs:`,
        runs404Json,
      ].join("\n")
    ).toHaveLength(0);

    // App console errors: must be [] after #518/#520 fixes.
    expect(
      appConsoleErrors,
      [
        `app-console-errors.json must be [] (tracked in #518/#520).`,
        `Found ${appConsoleErrors.length} app-owned error(s):`,
        consoleErrorsJson,
      ].join("\n")
    ).toHaveLength(0);

    // HTTP loopback hits: hosted HTTPS must not downgrade to HTTP loopback.
    // HTTPS loopback candidates are allowed under #517 unless they surface
    // app-owned console/network errors captured by app-console-errors.json.
    expect(
      loopbackHits,
      [
        `loopback-hits.json must be [] for disallowed HTTP loopback attempts (tracked in #517/#518).`,
        `Found ${loopbackHits.length} disallowed HTTP loopback attempt(s) from hosted origin:`,
        loopbackJson,
      ].join("\n")
    ).toHaveLength(0);
  });
});
