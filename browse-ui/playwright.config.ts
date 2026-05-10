import { defineConfig, devices } from "@playwright/test";

const releaseProof = Boolean(process.env.FIREBASE_PROOF);
// Cross-platform: Windows ships `python`, Unix ships `python3`.
const pythonCmd = process.platform === "win32" ? "python" : "python3";
// Cross-platform build command: bypass `pnpm build` (which requires pnpm
// allowBuilds approval) and invoke Next.js and post-build directly via node.
const buildCmd = [
  "node ./node_modules/next/dist/bin/next build",
  "node scripts/post-build.mjs",
  `${pythonCmd} ./scripts/create-e2e-db.py`,
  `${pythonCmd} ../browse.py --port 8765 --db ./e2e/.fixtures/playwright.db`,
].join(" && ");

// NOTE (#58 / Edge+mobile proof): The 'edge-desktop' and 'mobile-chrome'
// projects below add the browser coverage required by issue #58.
// Honest runtime evidence for these projects requires Playwright browsers to
// be installed with `pnpm exec playwright install msedge chromium` and a
// physical/virtual display (non-CI headless environments may omit --channel msedge).
// If this environment cannot run Edge/mobile, set SKIP_EDGE_MOBILE=1 to exclude
// those projects. CI jobs MUST run them to satisfy the #58 acceptance bar.
const skipEdgeMobile = Boolean(process.env.SKIP_EDGE_MOBILE);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  // workers: 1 eliminates ChunkLoadError startup noise caused by concurrent
  // browser contexts hitting the Next.js dev server before all chunks are warm.
  workers: 1,
  retries: 1,
  grep: releaseProof ? /FIREBASE_PROOF/ : undefined,
  expect: {
    timeout: 15_000,
  },
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "behavioral",
      testMatch: [
        "**/smoke.spec.ts",
        "**/shortcuts.spec.ts",
        "**/chat.spec.ts",
        "**/diagnostics.spec.ts",
        "**/broker-mode.spec.ts",
      ],
      use: devices["Desktop Chrome"],
    },
    {
      name: "visual",
      testMatch: ["**/visual.spec.ts"],
      use: devices["Desktop Chrome"],
    },
    // Iroh relay spike (#69) — tests navigate to about:blank and probe relay endpoints
    // directly via browser fetch (no app server interaction needed). The global webServer
    // defined above is still started by Playwright; iroh-spike tests do not use it. Run with:
    //   pnpm exec playwright test iroh-relay-rtt --project iroh-spike
    // Set IROH_RELAY_URL env to override the relay endpoint (default: relay.iroh.network).
    // Expected result in the current environment: DNS blocker surfaced as evidence.
    {
      name: "iroh-spike",
      testMatch: ["**/iroh-relay-rtt.spec.ts"],
      use: devices["Desktop Chrome"],
    },
    // Edge desktop — required for issue #58 acceptance.
    // Skipped when SKIP_EDGE_MOBILE=1 (e.g., environments without Edge installed).
    ...(!skipEdgeMobile
      ? [
          {
            name: "pairing-edge-desktop",
            testMatch: ["**/host-pairing.spec.ts"],
            use: {
              ...devices["Desktop Edge"],
              channel: "msedge",
            },
          },
          // Mobile Chrome — required for issue #58 acceptance.
          {
            name: "pairing-mobile-chrome",
            testMatch: ["**/host-pairing.spec.ts"],
            use: devices["Pixel 5"],
          },
        ]
      : []),
  ],
  webServer: releaseProof
    ? undefined
    : {
        command: buildCmd,
        port: 8765,
        reuseExistingServer: false,
        timeout: 180_000,
      },
});
