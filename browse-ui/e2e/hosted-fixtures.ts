/**
 * hosted-fixtures.ts — Playwright fixtures for hosted regression proof (#519).
 *
 * Key design constraints (from Q4/Q6 analysis):
 * - NO route stubs, especially not the glob pattern for api/operator/sessions wildcard/runs.
 *   The global runtimeErrorGuard in fixtures.ts stubs that route to mask real 404s.
 *   This fixture intentionally omits all stubs so real hosted network behaviour is
 *   captured as evidence.
 * - Console classification: app-owned errors go to `app-console-errors.json`;
 *   browser-extension-sourced noise goes to `extension-noise.json`;
 *   browser-internal (chrome-extension://, about:, devtools) goes to `browser-internal.json`;
 *   everything raw goes to `console-raw.json`.
 * - HTTP loopback requests from the hosted frame to 127.0.0.1:8765 / localhost:8765
 *   are captured in `loopback-hits.json`.
 * - 404 responses for `/api/operator/sessions/<CLI_SESSION_ID>/runs` are captured
 *   in `runs-404.json`.
 * - All artifacts are scrubbed of Authorization headers, cookies, token query params,
 *   JWTs (Bearer …), and pairing codes before writing.
 *
 * Usage: import { test, expect } from "./hosted-fixtures";
 */

import * as fs from "node:fs";
import * as path from "node:path";
import {
  test as base,
  expect,
  type ConsoleMessage,
  type Request,
  type Response,
} from "@playwright/test";

// ── Env ───────────────────────────────────────────────────────────────────────

export const HOSTED_URL = process.env.HOSTED_URL ?? "https://agents.linhngo.dev";
export const CLI_SESSION_ID = process.env.CLI_SESSION_ID ?? "";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ConsoleEntry {
  type: string;
  text: string;
  url?: string;
}

export interface RequestEntry {
  method: string;
  url: string;
  resourceType?: string;
}

export interface ResponseEntry {
  status: number;
  method: string;
  url: string;
}

export interface HostedArtifacts {
  /** All raw console messages captured during the test. */
  consoleRaw: ConsoleEntry[];
  /** App-owned console errors / page errors (non-extension, non-browser-internal). */
  appConsoleErrors: ConsoleEntry[];
  /** Browser extension noise (chrome-extension://, moz-extension://). */
  extensionNoise: ConsoleEntry[];
  /** Browser-internal messages (about:, devtools, etc.). */
  browserInternal: ConsoleEntry[];
  /** HTTP requests from the hosted frame to the local loopback backend. */
  loopbackHits: RequestEntry[];
  /** 404 responses for the CLI session /runs endpoint. */
  runs404: ResponseEntry[];
}

export interface HostedFixtures {
  /** Collected evidence artifacts for the current test. */
  hostedArtifacts: HostedArtifacts;
}

// ── Scrubbers ─────────────────────────────────────────────────────────────────

const SCRUB_PATTERNS: Array<[RegExp, string]> = [
  // Bearer tokens / JWTs
  [/Bearer\s+[A-Za-z0-9\-_.~+/]+=*/gi, "Bearer [REDACTED]"],
  // ?token= / &token= query params
  [/([?&]token=)[^&\s"']*/gi, "$1[REDACTED]"],
  // ?pairing_code= / &pairing_code=
  [/([?&]pairing_code=)[^&\s"']*/gi, "$1[REDACTED]"],
  // Authorization header values in logged strings
  [/(Authorization:\s*)[^\s"',]*/gi, "$1[REDACTED]"],
  // cookie= strings
  [/(cookie:\s*)[^\n"']*/gi, "$1[REDACTED]"],
  // JWT-shaped strings (three base64url segments)
  [/eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_.~+/]*/g, "[JWT-REDACTED]"],
];

function scrubString(s: string): string {
  let out = s;
  for (const [pattern, replacement] of SCRUB_PATTERNS) {
    out = out.replace(pattern, replacement);
  }
  return out;
}

function scrubUrl(url: string): string {
  try {
    const u = new URL(url);
    for (const key of ["token", "pairing_code", "access_token", "id_token"]) {
      if (u.searchParams.has(key)) {
        u.searchParams.set(key, "[REDACTED]");
      }
    }
    return scrubString(u.toString());
  } catch {
    return scrubString(url);
  }
}

// ── Classifiers ───────────────────────────────────────────────────────────────

/** Extension-sourced patterns (Chrome / Firefox extension URLs). */
const EXTENSION_SOURCE_RE = /^(?:chrome|moz|safari)-extension:\/\//i;

/** Browser-internal source patterns. */
const BROWSER_INTERNAL_SOURCE_RE = /^(?:about:|devtools:|chrome:\/\/)/i;

/** Loopback URLs — requests from the hosted frame to the local backend. */
const LOOPBACK_URL_RE = /^https?:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?\//i;

/**
 * Classify a console message into one of: app | extension | browser-internal.
 * Classification is based on the message URL (location), not just text content,
 * because the same error text could originate from either the app or an extension.
 */
function classifyConsole(message: ConsoleMessage): "app" | "extension" | "browser-internal" {
  const location = message.location();
  const url = location?.url ?? "";

  if (EXTENSION_SOURCE_RE.test(url)) return "extension";
  if (BROWSER_INTERNAL_SOURCE_RE.test(url)) return "browser-internal";
  return "app";
}

// ── Artifact writer ───────────────────────────────────────────────────────────

function writeArtifact(
  testInfo: { outputPath: (name: string) => string },
  name: string,
  data: unknown
): void {
  const filePath = testInfo.outputPath(name);
  const dir = path.dirname(filePath);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf-8");
}

// ── Fixture extension ─────────────────────────────────────────────────────────

/**
 * Hosted test fixture.
 *
 * - Registers page.on("console"), page.on("pageerror"), page.on("request"),
 *   page.on("response") collectors.
 * - Does NOT register any route stubs. All network requests go to the real server.
 * - After the test body, writes JSON artifact files to the Playwright output dir.
 */
export const test = base.extend<HostedFixtures>({
  hostedArtifacts: [
    async ({ page }, use, testInfo) => {
      const artifacts: HostedArtifacts = {
        consoleRaw: [],
        appConsoleErrors: [],
        extensionNoise: [],
        browserInternal: [],
        loopbackHits: [],
        runs404: [],
      };

      // ── Console collector ──────────────────────────────────────────────────
      page.on("console", (msg: ConsoleMessage) => {
        const entry: ConsoleEntry = {
          type: msg.type(),
          text: scrubString(msg.text()),
          url: scrubUrl(msg.location()?.url ?? ""),
        };
        artifacts.consoleRaw.push(entry);

        const cls = classifyConsole(msg);
        if (cls === "extension") {
          artifacts.extensionNoise.push(entry);
        } else if (cls === "browser-internal") {
          artifacts.browserInternal.push(entry);
        } else if (msg.type() === "error" || msg.type() === "warning") {
          // App-owned warnings and errors are evidence, not noise.
          artifacts.appConsoleErrors.push(entry);
        }
      });

      // ── Page error (uncaught exceptions) ──────────────────────────────────
      page.on("pageerror", (error: Error) => {
        const entry: ConsoleEntry = {
          type: "pageerror",
          text: scrubString(error.stack ?? error.message),
        };
        artifacts.consoleRaw.push(entry);
        artifacts.appConsoleErrors.push(entry);
      });

      // ── Request collector (loopback hits) ─────────────────────────────────
      page.on("request", (request: Request) => {
        const url = request.url();
        if (LOOPBACK_URL_RE.test(url)) {
          artifacts.loopbackHits.push({
            method: request.method(),
            url: scrubUrl(url),
            resourceType: request.resourceType(),
          });
        }
      });

      // ── Response collector (runs 404) ─────────────────────────────────────
      const sessionId = CLI_SESSION_ID;
      page.on("response", (response: Response) => {
        const url = response.url();
        const status = response.status();

        // Capture 404s for the /runs endpoint regardless of session ID presence
        // (catches both parameterised and static path variants).
        const isRunsPath =
          /\/api\/operator\/sessions\/[^/]+\/runs/.test(url) ||
          (sessionId && url.includes(`/sessions/${sessionId}/runs`));

        if (isRunsPath && status === 404) {
          artifacts.runs404.push({
            status,
            method: response.request().method(),
            url: scrubUrl(url),
          });
        }
      });

      // ── Run the test ───────────────────────────────────────────────────────
      await use(artifacts);

      // ── Write artifacts ────────────────────────────────────────────────────
      writeArtifact(testInfo, "console-raw.json", artifacts.consoleRaw);
      writeArtifact(testInfo, "app-console-errors.json", artifacts.appConsoleErrors);
      writeArtifact(testInfo, "extension-noise.json", artifacts.extensionNoise);
      writeArtifact(testInfo, "browser-internal.json", artifacts.browserInternal);
      writeArtifact(testInfo, "loopback-hits.json", artifacts.loopbackHits);
      writeArtifact(testInfo, "runs-404.json", artifacts.runs404);

      // Attach artifacts to the Playwright report for easy inspection.
      for (const name of [
        "console-raw.json",
        "app-console-errors.json",
        "extension-noise.json",
        "browser-internal.json",
        "loopback-hits.json",
        "runs-404.json",
      ]) {
        await testInfo.attach(name, {
          path: testInfo.outputPath(name),
          contentType: "application/json",
        });
      }
    },
    { auto: true },
  ],
});

export { expect };
