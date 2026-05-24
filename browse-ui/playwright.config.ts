import { defineConfig, devices } from "@playwright/test";

const releaseProof = Boolean(process.env.FIREBASE_PROOF);
// Hosted regression proof (#519) — navigates to real agents.linhngo.dev.
// Set HOSTED_PROOF=1 to run; normal `pnpm test:e2e` runs are unaffected.
const hostedProof = Boolean(process.env.HOSTED_PROOF);
const hostedUrl = process.env.HOSTED_URL ?? "https://agents.linhngo.dev";
// Cross-platform: Windows ships `python`, Unix ships `python3`.
const pythonCmd = process.platform === "win32" ? "python" : "python3";

// BROWSE_E2E_PORT allows overriding the local backend port when 8765 is busy
// (e.g. a hosted-bootstrap LaunchAgent owns it). Strictly validated as a
// digits-only integer in the safe user-port range to prevent shell injection
// into buildCmd. Defaults to 8765 to preserve CI behavior.
const DEFAULT_E2E_PORT = 8765;
function resolveE2EPort(): number {
  const raw = process.env.BROWSE_E2E_PORT;
  if (!raw) return DEFAULT_E2E_PORT;
  if (!/^[0-9]+$/.test(raw)) {
    throw new Error(`BROWSE_E2E_PORT must be digits only; got ${JSON.stringify(raw)}`);
  }
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1024 || n > 65535) {
    throw new Error(`BROWSE_E2E_PORT must be an integer in [1024, 65535]; got ${raw}`);
  }
  return n;
}
const e2ePort = resolveE2EPort();

// Cross-platform build command: bypass `pnpm build` (which requires pnpm
// allowBuilds approval) and invoke Next.js and post-build directly via node.
// --no-tls forces plain HTTP even when mkcert certs exist under ~/.copilot/certs,
// keeping Playwright's HTTP baseURL valid.
const buildCmd = [
  "node ./node_modules/next/dist/bin/next build",
  "node scripts/post-build.mjs",
  `${pythonCmd} ./scripts/create-e2e-db.py`,
  `${pythonCmd} ../browse.py --no-tls --port ${e2ePort} --db ./e2e/.fixtures/playwright.db`,
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
    baseURL: `http://127.0.0.1:${e2ePort}`,
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
    // hosted-regression project (#519) — only active when HOSTED_PROOF=1.
    // Targets https://agents.linhngo.dev (or HOSTED_URL override); does NOT
    // use the local webServer. No route stubs are registered (see
    // hosted-fixtures.ts). Default CI must NOT set HOSTED_PROOF=1.
    ...(hostedProof
      ? [
          {
            name: "hosted-regression",
            testMatch: ["**/hosted-regression.spec.ts"],
            use: {
              ...devices["Desktop Chrome"],
              baseURL: hostedUrl,
            },
          },
        ]
      : []),
  ],
  // Do not start the local webServer for release or hosted proof runs.
  webServer:
    releaseProof || hostedProof
      ? undefined
      : {
          command: buildCmd,
          port: e2ePort,
          reuseExistingServer: false,
          timeout: 180_000,
        },
});
