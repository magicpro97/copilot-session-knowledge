"use client";

/**
 * DiagnosticPanel — structured host connectivity failure surface.
 *
 * Replaces the generic "Cannot reach this host from a secure page" warning
 * with a reason + actionable fix list tailored to the browser and failure mode.
 *
 * Issue #56: diagnostic panel/probe surface.
 */

import { useEffect, useState } from "react";
import { Check, Copy, ExternalLink } from "lucide-react";

import type { HostCompatibility } from "@/lib/host-profiles";
import type { LocalBootstrapResult, DaemonState } from "@/lib/hosts/local-bootstrap";

// ── Browser detection ────────────────────────────────────────────────────────

export type BrowserKind = "chromium" | "safari" | "firefox" | "other";

/** Detect the current browser kind from the user-agent string (best-effort). */
export function detectBrowser(): BrowserKind {
  if (typeof navigator === "undefined") return "other";
  const ua = navigator.userAgent;
  if (/Firefox\//.test(ua)) return "firefox";
  if (/Safari\//.test(ua) && !/Chrome\//.test(ua)) return "safari";
  if (/Chrome\/|Edg\/|Chromium\//.test(ua)) return "chromium";
  return "other";
}

/** Returns true when the browser has documented PNA (Private Network Access) support. */
export function browserSupportsPna(browser: BrowserKind): boolean {
  return browser === "chromium";
}

/**
 * Detected browser info with kind and major version.
 * Version is null when it cannot be extracted from the UA string.
 */
export interface BrowserInfo {
  kind: BrowserKind;
  /** Major version number extracted from the UA string, or null if not available. */
  version: number | null;
}

/**
 * Detect browser kind and major version from the user-agent string.
 *
 * Version-aware so the UI can link to the correct docs page:
 * - Chrome 126+ renamed PNA to "Local Network Access" (LNA).
 * - Safari 17+ added local network access controls.
 */
export function detectBrowserInfo(): BrowserInfo {
  const kind = detectBrowser();
  if (typeof navigator === "undefined") return { kind, version: null };
  const ua = navigator.userAgent;
  let version: number | null = null;
  if (kind === "chromium") {
    const m = ua.match(/(?:Chrome|Edg)\/(\d+)/);
    if (m) version = parseInt(m[1], 10);
  } else if (kind === "firefox") {
    const m = ua.match(/Firefox\/(\d+)/);
    if (m) version = parseInt(m[1], 10);
  } else if (kind === "safari") {
    const m = ua.match(/Version\/(\d+)/);
    if (m) version = parseInt(m[1], 10);
  }
  return { kind, version };
}

/** Returns the docs URL for PNA/LNA for the given browser+version. */
export function getPnaDocsUrl(info: BrowserInfo): string {
  switch (info.kind) {
    case "chromium":
      // Chrome 126+ renamed the feature to "Local Network Access"
      if (info.version !== null && info.version >= 126) {
        return "https://developer.chrome.com/blog/local-network-access-permission-prompt";
      }
      return "https://developer.chrome.com/docs/privacy-security/private-network-access";
    case "safari":
      return "https://webkit.org/blog/14247/webkit-features-in-safari-17-0/";
    case "firefox":
      return "https://bugzilla.mozilla.org/show_bug.cgi?id=1437308";
    default:
      return "https://developer.chrome.com/docs/privacy-security/private-network-access";
  }
}

const BROWSER_LABELS: Record<BrowserKind, string> = {
  chromium: "Chrome / Edge (Chromium)",
  safari: "Safari",
  firefox: "Firefox",
  other: "Your browser",
};

const LOCAL_UI_URL = "https://127.0.0.1:8765/";
const LOCAL_UI_HTTP_FALLBACK_URL = "http://127.0.0.1:8765/";
const LOCAL_BROWSER_COMMAND = "browse --hosted-bootstrap --open-browser chrome";
const BROWSER_SCAN_COMMAND = "browse --list-browsers";

// ── CopyButton ────────────────────────────────────────────────────────────────

/**
 * Inline copy-to-clipboard button for code snippets.
 * Shows a brief checkmark animation after a successful copy.
 */
function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    if (typeof navigator === "undefined" || !navigator.clipboard) return;
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      aria-label={label ?? `Copy: ${text}`}
      title={copied ? "Copied!" : "Copy to clipboard"}
      className="text-muted-foreground hover:text-foreground focus-visible:ring-ring ml-1 inline-flex items-center rounded p-0.5 focus-visible:ring-1 focus-visible:outline-none"
    >
      {copied ? (
        <Check className="size-3 text-green-500" aria-hidden="true" />
      ) : (
        <Copy className="size-3" aria-hidden="true" />
      )}
    </button>
  );
}

/** Inline code snippet with an optional copy button. */
function CodeSnippet({ children, copyText }: { children: React.ReactNode; copyText?: string }) {
  return (
    <span className="inline-flex items-center gap-0.5">
      <code className="bg-muted rounded px-1 font-mono">{children}</code>
      {copyText !== undefined && <CopyButton text={copyText} />}
    </span>
  );
}

// ── Step item ────────────────────────────────────────────────────────────────

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-2.5">
      <span className="bg-muted text-muted-foreground flex h-5 w-5 flex-none items-center justify-center rounded-full text-[11px] font-medium">
        {n}
      </span>
      <span className="text-muted-foreground text-xs leading-relaxed">{children}</span>
    </li>
  );
}

function LocalUiLink({ children }: { children: React.ReactNode }) {
  return (
    <a
      href={LOCAL_UI_URL}
      className="font-mono text-blue-600 underline underline-offset-2 dark:text-blue-400"
      target="_blank"
      rel="noreferrer"
    >
      {children}
    </a>
  );
}

function ConfiguredBrowserFallback({ browserInfo }: { browserInfo: BrowserInfo }) {
  const label = BROWSER_LABELS[browserInfo.kind];
  const message =
    browserInfo.kind === "chromium"
      ? "Chrome/Edge can use Local Network Access prompts, but opening the local app directly avoids hosted-to-local browser blocking."
      : browserInfo.kind === "firefox"
        ? "Firefox does not implement Chromium PNA/LNA; use Chrome/Edge for hosted detection, or open the local app directly."
        : browserInfo.kind === "safari"
          ? "Safari is not supported for hosted-to-local connection recovery. Use Chrome/Edge, or open the local app directly."
          : "This browser may block hosted-to-local requests. Use Chrome/Edge when possible, or open the local app directly.";

  return (
    <div
      className="mb-3 space-y-2 rounded-lg border border-blue-500/30 bg-blue-500/5 p-3"
      data-testid="configured-browser-fallback"
      role="status"
      aria-label="Configured browser fallback"
    >
      <p className="text-xs font-semibold text-blue-700 dark:text-blue-300">
        Open a browser that can use the local backend
      </p>
      <p className="text-muted-foreground text-xs">
        Current browser: <span className="font-medium">{label}</span>. {message}
      </p>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <a
          href={LOCAL_UI_URL}
          target="_blank"
          rel="noreferrer"
          data-testid="open-configured-browser-cta"
          className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 font-medium"
        >
          Open local app
          <ExternalLink className="size-3" aria-hidden="true" />
        </a>
        <span className="text-muted-foreground">
          Scan installed Chrome/Edge/Firefox locally with{" "}
          <CodeSnippet copyText={BROWSER_SCAN_COMMAND}>{BROWSER_SCAN_COMMAND}</CodeSnippet>.
        </span>
      </div>
      <p className="text-muted-foreground text-xs">
        If your backend was started with <CodeSnippet copyText="--no-tls">--no-tls</CodeSnippet>,
        use{" "}
        <a
          href={LOCAL_UI_HTTP_FALLBACK_URL}
          target="_blank"
          rel="noreferrer"
          data-testid="open-configured-browser-http-fallback"
          className="font-mono text-blue-600 underline underline-offset-2 dark:text-blue-400"
        >
          {LOCAL_UI_HTTP_FALLBACK_URL}
        </a>{" "}
        instead.
      </p>
      <p className="text-muted-foreground text-xs">
        To start the backend and open Chrome without installing a new browser, run{" "}
        <CodeSnippet copyText={LOCAL_BROWSER_COMMAND}>{LOCAL_BROWSER_COMMAND}</CodeSnippet>. No
        browser security-bypass flags are used.
      </p>
    </div>
  );
}

// ── Panel variants ────────────────────────────────────────────────────────────

/** Hard incompatibility: HTTPS origin → non-loopback HTTP host (mixed content). */
function MixedContentPanel({ hostUrl }: { hostUrl: string }) {
  return (
    <div
      className="border-destructive/30 bg-destructive/5 space-y-2 rounded-lg border p-3"
      data-testid="diagnostic-panel"
      role="alert"
      aria-label="Connectivity diagnostic: mixed content"
    >
      <p className="text-destructive text-xs font-semibold">✗ Mixed content — cannot connect</p>
      <p className="text-muted-foreground text-xs">
        A secure (HTTPS) page cannot load resources from the insecure HTTP host{" "}
        <CodeSnippet copyText={hostUrl || ""}>{hostUrl || "(unknown)"}</CodeSnippet>. Browsers block
        this as mixed content regardless of browser settings.
      </p>
      <p className="text-foreground text-xs font-medium">Fix options:</p>
      <ol className="list-none space-y-1.5">
        <Step n={1}>
          Expose your backend via an HTTPS tunnel, e.g.{" "}
          <CodeSnippet copyText="ngrok http 8765">ngrok http 8765</CodeSnippet>, then add the tunnel
          URL as a host in Settings → Hosts.
        </Step>
        <Step n={2}>
          Or open the browse UI directly at <LocalUiLink>{LOCAL_UI_URL}</LocalUiLink> to stay fully
          same-origin without mixed content restrictions.
        </Step>
      </ol>
    </div>
  );
}

/** PNA-required: HTTPS origin → HTTP loopback, depends on browser PNA support. */
function PnaRequiredPanel({ hostUrl, browserInfo }: { hostUrl: string; browserInfo: BrowserInfo }) {
  const pnaSupported = browserSupportsPna(browserInfo.kind);
  const label = BROWSER_LABELS[browserInfo.kind];
  const docsUrl = getPnaDocsUrl(browserInfo);
  const versionLabel = browserInfo.version !== null ? ` ${browserInfo.version}` : "";
  const isLna =
    browserInfo.kind === "chromium" && browserInfo.version !== null && browserInfo.version >= 126;
  const featureName = isLna ? "Local Network Access (LNA)" : "Private Network Access (PNA)";

  return (
    <div
      className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-50/50 p-3 dark:bg-amber-950/20"
      data-testid="diagnostic-panel"
      role="alert"
      aria-label="Connectivity diagnostic: Private Network Access required"
    >
      <p className="text-xs font-semibold text-amber-700 dark:text-amber-400">
        ⚠ {featureName} required
      </p>
      <p className="text-muted-foreground text-xs">
        Connecting from HTTPS to{" "}
        <CodeSnippet copyText={hostUrl || "localhost"}>{hostUrl || "localhost"}</CodeSnippet>{" "}
        requires{" "}
        <a
          href={docsUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-0.5 underline underline-offset-2"
          aria-label={`${featureName} documentation`}
        >
          <abbr
            title={`${featureName} — browser security feature controlling HTTPS-to-local connections`}
          >
            {featureName}
          </abbr>
          <ExternalLink className="size-2.5" aria-hidden="true" />
        </a>{" "}
        support.{" "}
        {pnaSupported
          ? `${label}${versionLabel} supports ${isLna ? "LNA" : "PNA"} — ensure the backend opt-in flags are set.`
          : `${label}${versionLabel} does not currently support direct loopback from HTTPS.`}
      </p>
      <p className="text-foreground text-xs font-medium">Fix options:</p>
      <ol className="list-none space-y-1.5">
        {pnaSupported ? (
          <>
            <Step n={1}>
              Restart the local backend with the hosted-bootstrap flag:{" "}
              <CodeSnippet copyText="browse --hosted-bootstrap">
                browse --hosted-bootstrap
              </CodeSnippet>
              . This sets the required{" "}
              <CodeSnippet copyText="Access-Control-Allow-Private-Network: true">
                Access-Control-Allow-Private-Network
              </CodeSnippet>{" "}
              response headers that {label} needs for {isLna ? "LNA" : "PNA"} preflight.
            </Step>
            <Step n={2}>
              If the backend is already running with{" "}
              <CodeSnippet copyText="--hosted-bootstrap">--hosted-bootstrap</CodeSnippet>, reload
              the page and try connecting again — the {isLna ? "LNA" : "PNA"} preflight should pass.
            </Step>
          </>
        ) : (
          <>
            <Step n={1}>
              Use an HTTPS tunnel:{" "}
              <CodeSnippet copyText="ngrok http 8765">ngrok http 8765</CodeSnippet>, then save the
              tunnel URL as a remote host. This bypasses {featureName} entirely.
            </Step>
            <Step n={2}>
              Or switch to Chrome or Edge (Chromium) to use native {isLna ? "LNA" : "PNA"} support
              with{" "}
              <CodeSnippet copyText="browse --hosted-bootstrap">
                browse --hosted-bootstrap
              </CodeSnippet>
              .
            </Step>
          </>
        )}
      </ol>
    </div>
  );
}

/**
 * Daemon is not running (connection refused on loopback probe).
 * Shows "start daemon" instructions.
 */
function DaemonNotRunningPanel() {
  return (
    <div
      className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-50/50 p-3 dark:bg-amber-950/20"
      data-testid="diagnostic-panel"
      role="alert"
      aria-label="Connectivity diagnostic: daemon not running"
    >
      <p className="text-xs font-semibold text-amber-700 dark:text-amber-400">
        ⚠ Local backend not running
      </p>
      <p className="text-muted-foreground text-xs">
        No backend was found on the local loopback addresses. Start a local backend with
        hosted-bootstrap support so the hosted UI can connect to it.
      </p>
      <p className="text-foreground text-xs font-medium">Fix options:</p>
      <ol className="list-none space-y-1.5">
        <Step n={1}>
          Start the local backend with hosted-bootstrap support:{" "}
          <CodeSnippet copyText="browse --hosted-bootstrap">browse --hosted-bootstrap</CodeSnippet>
        </Step>
        <Step n={2}>
          Expose it via HTTPS tunnel:{" "}
          <CodeSnippet copyText="ngrok http 8765">ngrok http 8765</CodeSnippet>, then go to{" "}
          <strong>Settings → Hosts</strong> and add the tunnel URL as a remote agent host.
        </Step>
        <Step n={3}>
          Or open the local browse app at <LocalUiLink>{LOCAL_UI_URL}</LocalUiLink> directly (no
          hosted shell needed).
        </Step>
      </ol>
    </div>
  );
}

/**
 * Daemon is running but without `--hosted-bootstrap` (no PNA/CORS headers).
 * Shows "restart with --hosted-bootstrap" instructions.
 */
function DaemonNoPnaPanel() {
  return (
    <div
      className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-50/50 p-3 dark:bg-amber-950/20"
      data-testid="diagnostic-panel"
      role="alert"
      aria-label="Connectivity diagnostic: daemon running without --hosted-bootstrap"
    >
      <p className="text-xs font-semibold text-amber-700 dark:text-amber-400">
        ⚠ Backend running — but missing <code className="font-mono">--hosted-bootstrap</code>
      </p>
      <p className="text-muted-foreground text-xs">
        A local backend is listening, but it was started without{" "}
        <CodeSnippet copyText="--hosted-bootstrap">--hosted-bootstrap</CodeSnippet>. The hosted UI
        needs Private Network Access / CORS headers that this flag enables.
      </p>
      <p className="text-foreground text-xs font-medium">Fix options:</p>
      <ol className="list-none space-y-1.5">
        <Step n={1}>
          Stop the current backend and restart it with:{" "}
          <CodeSnippet copyText="browse --hosted-bootstrap">browse --hosted-bootstrap</CodeSnippet>
        </Step>
        <Step n={2}>
          After restarting, click <strong>Detect local backend</strong> in the host panel or reload
          this page.
        </Step>
      </ol>
    </div>
  );
}

/**
 * All loopback probes from a hosted HTTPS origin returned "unknown" — the local
 * backend does not serve HTTPS, so the HTTPS loopback probes were rejected by TLS
 * or the backend is not running at all. The browser never attempted HTTP loopback
 * because HTTP loopback is not probed from HTTPS origin (issue #517).
 * Guides the user to HTTPS-local-backend, mkcert, HTTPS tunnel, or direct URL.
 */
function HostedHttpsUnknownPanel() {
  return (
    <div
      className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-50/50 p-3 dark:bg-amber-950/20"
      data-testid="diagnostic-panel"
      role="alert"
      aria-label="Connectivity diagnostic: HTTPS local backend required"
    >
      <p className="text-xs font-semibold text-amber-700 dark:text-amber-400">
        ⚠ HTTPS local backend required
      </p>
      <p className="text-muted-foreground text-xs">
        From a hosted HTTPS page, browsers block HTTP loopback requests. Probes to{" "}
        <CodeSnippet copyText="https://127.0.0.1:8765">https://127.0.0.1:8765</CodeSnippet> and{" "}
        <CodeSnippet copyText="https://localhost:8765">https://localhost:8765</CodeSnippet> received
        no valid response — either no backend was detected at these addresses, or it does not yet
        serve HTTPS. To connect from the hosted shell your local backend must be reachable over
        HTTPS.
      </p>
      <p className="text-foreground text-xs font-medium">Fix options:</p>
      <ol className="list-none space-y-1.5">
        <Step n={1}>
          Use an HTTPS tunnel: <CodeSnippet copyText="ngrok http 8765">ngrok http 8765</CodeSnippet>
          , then add the tunnel URL in <strong>Settings → Hosts</strong>. This is the quickest path
          with no TLS setup.
        </Step>
        <Step n={2}>
          Run the local backend with a trusted TLS certificate via{" "}
          <a
            href="https://github.com/FiloSottile/mkcert"
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2"
          >
            mkcert
          </a>
          :{" "}
          <CodeSnippet copyText="mkcert -install && mkcert localhost 127.0.0.1">
            mkcert -install &amp;&amp; mkcert localhost 127.0.0.1
          </CodeSnippet>
          , then restart with the generated cert paths. (Native HTTPS backend tracked in issue #36.)
        </Step>
        <Step n={3}>
          Or open the local browse UI directly at <LocalUiLink>{LOCAL_UI_URL}</LocalUiLink> — all
          features work from that origin without HTTPS restrictions.
        </Step>
      </ol>
    </div>
  );
}

/**
 * All loopback probes from an HTTP origin returned "unknown" daemon state — most likely
 * Chrome PNA (Private Network Access) is silently blocking the request at the network stack.
 * Guides the user to the direct localhost URL or tunnel alternative.
 * (For HTTPS origin unknown state, see HostedHttpsUnknownPanel.)
 */
function PnaBlockedPanel() {
  return (
    <div
      className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-50/50 p-3 dark:bg-amber-950/20"
      data-testid="diagnostic-panel"
      role="alert"
      aria-label="Connectivity diagnostic: browser blocking local connection"
    >
      <p className="text-xs font-semibold text-amber-700 dark:text-amber-400">
        ⚠ Browser blocking local backend connection
      </p>
      <p className="text-muted-foreground text-xs">
        Chrome&apos;s Private Network Access (PNA) security policy is blocking connections from this
        hosted page to your local backend. This is a browser-level restriction that cannot be
        bypassed from the web page.
      </p>
      <p className="text-foreground text-xs font-medium">Recommended:</p>
      <ol className="list-none space-y-1.5">
        <Step n={1}>
          Open the local browse UI directly at <LocalUiLink>{LOCAL_UI_URL}</LocalUiLink> — all
          features work without PNA restrictions.
        </Step>
        <Step n={2}>
          Or use a tunnel: <CodeSnippet copyText="ngrok http 8765">ngrok http 8765</CodeSnippet>,
          then add the tunnel URL in <strong>Settings → Hosts</strong>.
        </Step>
      </ol>
    </div>
  );
}

/** Hosted origin with no remote agent host configured at all. */
function NoHostConfiguredPanel() {
  return (
    <div
      className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-50/50 p-3 dark:bg-amber-950/20"
      data-testid="diagnostic-panel"
      role="alert"
      aria-label="Connectivity diagnostic: no host configured"
    >
      <p className="text-xs font-semibold text-amber-700 dark:text-amber-400">
        ⚠ No agent host configured
      </p>
      <p className="text-muted-foreground text-xs">
        This hosted web app has no remote agent host configured. It cannot browse workspaces or
        start sessions until a reachable backend is connected.
      </p>
      <p className="text-foreground text-xs font-medium">Fix options:</p>
      <ol className="list-none space-y-1.5">
        <Step n={1}>
          Start a local backend with hosted-bootstrap support:{" "}
          <CodeSnippet copyText="browse --hosted-bootstrap">browse --hosted-bootstrap</CodeSnippet>
        </Step>
        <Step n={2}>
          Expose it via HTTPS: <CodeSnippet copyText="ngrok http 8765">ngrok http 8765</CodeSnippet>
        </Step>
        <Step n={3}>
          Go to <strong>Settings → Hosts</strong> and add the tunnel URL as a remote agent host.
        </Step>
        <Step n={4}>
          Or open the local browse app at <LocalUiLink>{LOCAL_UI_URL}</LocalUiLink> directly (no
          hosted shell needed).
        </Step>
      </ol>
    </div>
  );
}

/** Backend was found via loopback probe but requires a manual auth token. */
function AuthRequiredPanel({ hostUrl }: { hostUrl: string }) {
  return (
    <div
      className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-50/50 p-3 dark:bg-amber-950/20"
      data-testid="diagnostic-panel"
      role="alert"
      aria-label="Connectivity diagnostic: authentication required"
    >
      <p className="text-xs font-semibold text-amber-700 dark:text-amber-400">
        ⚠ Backend requires authentication
      </p>
      <p className="text-muted-foreground text-xs">
        A backend was detected at <CodeSnippet copyText={hostUrl}>{hostUrl}</CodeSnippet>, but it
        requires a token. Auto-connection is not possible without a token.
      </p>
      <p className="text-foreground text-xs font-medium">Fix options:</p>
      <ol className="list-none space-y-1.5">
        <Step n={1}>
          Go to <strong>Settings → Hosts</strong> to pair via QR code or enter your auth token
          manually.
        </Step>
        <Step n={2}>
          Or restart the backend in open-auth mode (no token required) for local development use.
        </Step>
      </ol>
    </div>
  );
}

// ── Public interface ──────────────────────────────────────────────────────────

export interface DiagnosticPanelProps {
  /** Result of checkHostCompatibility(). Null means no compat assessment yet. */
  compat: HostCompatibility | null;
  /** Latest probe result from probeLocalBootstrap(). Optional context. */
  probeResult?: LocalBootstrapResult | null;
  /** The host base URL being checked (for display in messages). */
  hostUrl?: string;
  /** Whether the current page is served from a hosted (non-loopback) static origin. */
  isHosted: boolean;
}

/**
 * DiagnosticPanel — structured host connectivity failure surface.
 *
 * Replaces the generic "Cannot reach this host from a secure page" warning
 * with a reason-code + actionable fix list tailored to the browser and failure mode.
 *
 * Covers seven distinct failure classes:
 * - `mixed-content-http`: HTTPS → non-loopback HTTP (hard browser block).
 * - `pna-required`: HTTPS → HTTP loopback (Private Network Access needed; browser-specific).
 * - Daemon not running: loopback probe shows connection refused.
 * - Daemon running without `--hosted-bootstrap`: probe blocked by PNA/CORS, server is up.
 * - `auth-required`: Backend found but needs a user-supplied token.
 * - HTTPS local backend required: HTTPS-origin probed HTTPS candidates, all unknown (TLS absent).
 * - No host configured: Hosted static origin with no remote agent host added.
 *
 * Returns null when there is nothing actionable to display.
 *
 * Browser detection is deferred to `useEffect` to avoid SSR/hydration mismatches
 * (server always returns kind="other", version=null; client updates after first paint).
 */
export function DiagnosticPanel({
  compat,
  probeResult,
  hostUrl = "",
  isHosted,
}: DiagnosticPanelProps) {
  // Defer browser detection to client to avoid SSR/hydration mismatch.
  const [browserInfo, setBrowserInfo] = useState<BrowserInfo>({ kind: "other", version: null });
  useEffect(() => {
    setBrowserInfo(detectBrowserInfo());
  }, []);

  const withBrowserFallback = (panel: React.ReactNode) => (
    <>
      {isHosted ? <ConfiguredBrowserFallback browserInfo={browserInfo} /> : null}
      {panel}
    </>
  );

  // Hard incompatibility: mixed-content from HTTPS to non-loopback HTTP.
  if (compat && !compat.compatible && compat.code === "mixed-content-http") {
    return withBrowserFallback(<MixedContentPanel hostUrl={hostUrl} />);
  }

  // PNA-required: HTTPS → HTTP loopback, needs browser PNA support.
  if (compat?.compatible && compat.code === "pna-required") {
    return withBrowserFallback(<PnaRequiredPanel hostUrl={hostUrl} browserInfo={browserInfo} />);
  }

  // Backend probed but requires auth token (daemon is running, just needs pairing).
  if (probeResult?.status === "auth-required") {
    return withBrowserFallback(<AuthRequiredPanel hostUrl={probeResult.url} />);
  }

  // Probe ran and returned unavailable — surface daemon-specific guidance.
  if (probeResult?.status === "unavailable") {
    const reasons = probeResult.reasons ?? [];
    // Collect daemon states from all attempted candidates.
    const daemonStates = new Set<DaemonState | undefined>(reasons.map((r) => r.daemonState));

    if (daemonStates.has("running-no-pna")) {
      // At least one candidate had a server listening but no PNA headers.
      return withBrowserFallback(<DaemonNoPnaPanel />);
    }
    if (daemonStates.has("not-running") && !daemonStates.has("unknown")) {
      // All candidates with a daemon state concluded "not running".
      return withBrowserFallback(<DaemonNotRunningPanel />);
    }
    // All probes returned "unknown" daemon state.
    // Distinguish by candidate URL scheme:
    //   • HTTPS candidates (https://) → backend needs HTTPS/TLS; show mkcert/tunnel guidance.
    //   • HTTP candidates (http://)   → Chrome PNA blocking at network stack; show direct-URL guidance.
    if (daemonStates.has("unknown") && isHosted) {
      const unknownReasons = reasons.filter((r) => r.daemonState === "unknown");
      const allHttpsCandidates =
        unknownReasons.length > 0 && unknownReasons.every((r) => r.url.startsWith("https://"));
      if (allHttpsCandidates) {
        return withBrowserFallback(<HostedHttpsUnknownPanel />);
      }
      return withBrowserFallback(<PnaBlockedPanel />);
    }
    // Inconclusive daemon state — fall through to NoHostConfiguredPanel below.
  }

  // Hosted origin with no hard compat issue but no remote host configured.
  if (isHosted) {
    return withBrowserFallback(<NoHostConfiguredPanel />);
  }

  // Local origin with no compat issue — nothing actionable to show.
  return null;
}
