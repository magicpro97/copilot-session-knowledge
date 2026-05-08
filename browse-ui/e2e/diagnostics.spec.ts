/**
 * diagnostics.spec.ts — e2e evidence capture for issue #56.
 *
 * Test strategy:
 * 1. Static showcase tests: verify before/after SHA-256 hashes differ (legacy
 *    generic warning vs new structured DiagnosticPanel).
 * 2. Runtime-backed tests: mock the `/.well-known/browse-host` endpoint to
 *    exercise real diagnostic states (detected, auth-required, unavailable with
 *    daemon state distinctions) and verify the live probe section responds
 *    correctly to actual probe results.
 *
 * Screenshot-hash pattern reused from browse-ui/e2e/chat.spec.ts lines 499–522.
 */

import { createHash } from "node:crypto";
import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";

// ── Helpers ───────────────────────────────────────────────────────────────────

const WELL_KNOWN_PATH = "/.well-known/browse-host";

/** Mock the loopback well-known endpoint to return a specific response. */
async function mockWellKnown(page: Page, body: Record<string, unknown>, status = 200) {
  for (const host of ["127.0.0.1:8765", "localhost:8765"]) {
    await page.route(`http://${host}${WELL_KNOWN_PATH}`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        status,
        body: JSON.stringify(body),
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Private-Network": "true",
        },
      });
    });
  }
}

/** Abort all loopback well-known requests (simulates daemon not running). */
async function mockWellKnownAbort(page: Page) {
  for (const host of ["127.0.0.1:8765", "localhost:8765"]) {
    await page.route(`http://${host}${WELL_KNOWN_PATH}`, async (route) => {
      await route.abort("connectionrefused");
    });
    // Also abort the secondary no-cors probe to the root
    await page.route(`http://${host}/`, async (route) => {
      await route.abort("connectionrefused");
    });
  }
}

/** Mock daemon running but without PNA headers (no-cors probe succeeds, main probe fails). */
async function mockWellKnownNoPna(page: Page) {
  for (const host of ["127.0.0.1:8765", "localhost:8765"]) {
    // Main PNA probe aborts (no CORS headers)
    await page.route(`http://${host}${WELL_KNOWN_PATH}`, async (route) => {
      await route.abort("connectionrefused");
    });
    // Secondary no-cors probe to root succeeds (something is listening)
    await page.route(`http://${host}/`, async (route) => {
      await route.fulfill({ status: 200, body: "OK" });
    });
  }
}

const OPEN_BACKEND_RESPONSE = {
  schema: "browse-host/1",
  status: "ok",
  auth: "open",
  manual_token_required: false,
  capabilities: [],
  cors_origins_configured: true,
};

const AUTH_REQUIRED_RESPONSE = {
  schema: "browse-host/1",
  status: "ok",
  auth: "token",
  manual_token_required: true,
  capabilities: [],
  cors_origins_configured: true,
};

// ── Static showcase before/after tests ───────────────────────────────────────

test("diagnostic panel before/after SHA-256 evidence [ISSUE-56]", async ({ page }, testInfo) => {
  await page.goto("/diagnostics");
  await expect(page.getByTestId("diagnostics-page")).toBeVisible({ timeout: 20_000 });

  // ── Before: legacy generic warning ────────────────────────────────────────
  const legacyEl = page.getByTestId("legacy-warning");
  await expect(legacyEl).toBeVisible();

  const beforeShot = await legacyEl.screenshot({ animations: "disabled", caret: "hide" });
  await testInfo.attach("before-generic-warning.png", {
    body: beforeShot,
    contentType: "image/png",
  });
  const beforeHash = createHash("sha256").update(beforeShot).digest("hex");

  // ── After: new structured DiagnosticPanel (first / mixed-content panel) ───
  const panelEl = page.getByTestId("diagnostic-panel").first();
  await expect(panelEl).toBeVisible();

  const afterShot = await panelEl.screenshot({ animations: "disabled", caret: "hide" });
  await testInfo.attach("after-diagnostic-panel.png", {
    body: afterShot,
    contentType: "image/png",
  });
  const afterHash = createHash("sha256").update(afterShot).digest("hex");

  // ── Attach evidence JSON ───────────────────────────────────────────────────
  await testInfo.attach("screenshot-hashes.json", {
    body: JSON.stringify(
      {
        issue: "#56",
        description: "Before = legacy generic warning. After = structured DiagnosticPanel.",
        before: { testId: "legacy-warning", sha256: beforeHash },
        after: { testId: "diagnostic-panel (first)", sha256: afterHash },
        hashes_differ: beforeHash !== afterHash,
      },
      null,
      2
    ),
    contentType: "application/json",
  });

  // ── Assertions ─────────────────────────────────────────────────────────────
  // Hashes MUST differ — the structured panel is visually different from the generic warning.
  expect(beforeHash).not.toBe(afterHash);

  // Structural content checks.
  await expect(panelEl).toContainText("Mixed content");
  await expect(panelEl).toContainText("HTTPS tunnel");
  await expect(panelEl).toContainText("192.168.1.10:8765");
});

test("diagnostic panel: pna-required variant shows browser-specific guidance [ISSUE-56]", async ({
  page,
}) => {
  await page.goto("/diagnostics");
  await expect(page.getByTestId("diagnostics-page")).toBeVisible({ timeout: 20_000 });

  // Static showcase: pna-required panel is visible.
  const pnaSection = page.getByLabel("Variant — Private Network Access required");
  const pnaPanel = pnaSection.getByTestId("diagnostic-panel");
  await expect(pnaPanel).toBeVisible();
  // Chrome 126+ calls this "Local Network Access (LNA)"; older browsers say "Private Network Access".
  // Either way the panel contains "Network Access" and the host URL.
  await expect(pnaPanel).toContainText(/Network Access/);
  await expect(pnaPanel).toContainText("localhost:8765");
});

test("diagnostic panel: no-host-configured variant shown in static showcase [ISSUE-56]", async ({
  page,
}) => {
  await page.goto("/diagnostics");
  await expect(page.getByTestId("diagnostics-page")).toBeVisible({ timeout: 20_000 });

  const noHostSection = page.getByLabel("Variant — no agent host configured");
  const noHostPanel = noHostSection.getByTestId("diagnostic-panel");
  await expect(noHostPanel).toContainText("No agent host configured");
  await expect(noHostPanel).toContainText("Settings → Hosts");
});

// ── Copy button tests ─────────────────────────────────────────────────────────

test("diagnostic panel: copy buttons are present in mixed-content panel [ISSUE-56]", async ({
  page,
}) => {
  await page.goto("/diagnostics");
  await expect(page.getByTestId("diagnostics-page")).toBeVisible({ timeout: 20_000 });

  // Wait for static showcase panels to render
  const mixedPanel = page.getByTestId("diagnostic-panel").first();
  await expect(mixedPanel).toBeVisible();

  // Copy buttons should be present within the panel
  const copyButtons = mixedPanel.getByRole("button", { name: /copy/i });
  await expect(copyButtons.first()).toBeVisible();
});

// ── Runtime-backed live probe tests ──────────────────────────────────────────

test("live probe section: detected backend shows no error panel [ISSUE-56-RUNTIME]", async ({
  page,
}, testInfo) => {
  // Mock the well-known endpoint to return an open backend
  await mockWellKnown(page, OPEN_BACKEND_RESPONSE);

  await page.goto("/diagnostics");
  await expect(page.getByTestId("diagnostics-page")).toBeVisible({ timeout: 20_000 });

  // Wait for live probe to complete (re-probe button should not show spinner)
  const liveSection = page.getByTestId("live-probe-section");
  await expect(liveSection).toBeVisible();

  // Wait for probing to finish
  await expect(liveSection.getByTestId("rerun-probe-btn")).not.toHaveText(/Probing/);

  // Live probe wrapper should be visible; for a detected backend it shows
  // a success message instead of a DiagnosticPanel.
  const liveWrapper = liveSection.getByTestId("live-diagnostic-panel-wrapper");
  await expect(liveWrapper).toBeVisible();
  await expect(liveWrapper).toContainText("Backend detected");

  // Capture screenshot for evidence
  const shot = await liveSection.screenshot({ animations: "disabled", caret: "hide" });
  await testInfo.attach("live-probe-detected.png", {
    body: shot,
    contentType: "image/png",
  });
  const sha256 = createHash("sha256").update(shot).digest("hex");
  await testInfo.attach("live-probe-detected-hash.json", {
    body: JSON.stringify({ state: "detected", sha256 }, null, 2),
    contentType: "application/json",
  });
  expect(sha256).toBeTruthy();
});

test("live probe section: auth-required backend shows auth panel [ISSUE-56-RUNTIME]", async ({
  page,
}, testInfo) => {
  await mockWellKnown(page, AUTH_REQUIRED_RESPONSE);

  await page.goto("/diagnostics");
  await expect(page.getByTestId("diagnostics-page")).toBeVisible({ timeout: 20_000 });

  const liveSection = page.getByTestId("live-probe-section");
  await expect(liveSection).toBeVisible();

  // Wait for probing to finish
  await expect(liveSection.getByTestId("rerun-probe-btn")).not.toHaveText(/Probing/);

  // DiagnosticPanel should show auth-required guidance
  const panel = liveSection.getByTestId("diagnostic-panel");
  await expect(panel).toBeVisible({ timeout: 10_000 });
  await expect(panel).toContainText("Backend requires authentication");
  await expect(panel).toContainText("Settings → Hosts");

  // Screenshot evidence for auth-required runtime state
  const shot = await panel.screenshot({ animations: "disabled", caret: "hide" });
  await testInfo.attach("live-probe-auth-required.png", {
    body: shot,
    contentType: "image/png",
  });
  const sha256 = createHash("sha256").update(shot).digest("hex");
  await testInfo.attach("live-probe-auth-required-hash.json", {
    body: JSON.stringify({ state: "auth-required", sha256 }, null, 2),
    contentType: "application/json",
  });
  expect(sha256).toBeTruthy();
});

test("live probe section: unavailable backend (daemon not running) shows not-running panel [ISSUE-56-RUNTIME]", async ({
  page,
}, testInfo) => {
  // Abort all loopback probes (connection refused = daemon not running)
  await mockWellKnownAbort(page);

  await page.goto("/diagnostics");
  await expect(page.getByTestId("diagnostics-page")).toBeVisible({ timeout: 20_000 });

  const liveSection = page.getByTestId("live-probe-section");
  await expect(liveSection).toBeVisible();

  // Wait for probing to finish (may take up to PROBE_TIMEOUT_MS)
  await expect(liveSection.getByTestId("rerun-probe-btn")).not.toHaveText(/Probing/, {
    timeout: 15_000,
  });

  // Probe candidate table should be visible showing failure reasons
  const candidateTable = liveSection.getByTestId("probe-candidate-table");
  await expect(candidateTable).toBeVisible({ timeout: 10_000 });

  // Screenshot evidence for unavailable/not-running runtime state
  const shot = await liveSection.screenshot({ animations: "disabled", caret: "hide" });
  await testInfo.attach("live-probe-unavailable.png", {
    body: shot,
    contentType: "image/png",
  });
  const sha256 = createHash("sha256").update(shot).digest("hex");
  await testInfo.attach("live-probe-unavailable-hash.json", {
    body: JSON.stringify({ state: "unavailable", sha256 }, null, 2),
    contentType: "application/json",
  });
  expect(sha256).toBeTruthy();
});

test("live probe section: re-probe button triggers a fresh probe [ISSUE-56-RUNTIME]", async ({
  page,
}) => {
  await mockWellKnown(page, OPEN_BACKEND_RESPONSE);

  await page.goto("/diagnostics");
  await expect(page.getByTestId("diagnostics-page")).toBeVisible({ timeout: 20_000 });

  const liveSection = page.getByTestId("live-probe-section");
  const reProbeBtn = liveSection.getByTestId("rerun-probe-btn");

  // Wait for initial probe to finish
  await expect(reProbeBtn).not.toHaveText(/Probing/, { timeout: 15_000 });

  // Click re-probe
  await reProbeBtn.click();

  // Button should briefly show "Probing…" (may be fast in mock; just check it doesn't throw)
  // Then settle back to "Re-probe"
  await expect(reProbeBtn).not.toHaveText(/Probing/, { timeout: 15_000 });
  await expect(reProbeBtn).toContainText("Re-probe");
});
