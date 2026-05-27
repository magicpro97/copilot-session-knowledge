"use client";

import { useEffect, useState, type ComponentProps } from "react";
import {
  Activity,
  AlertCircle,
  Check,
  CheckCircle2,
  Copy,
  Globe,
  Info,
  Loader2,
  Network,
  Plus,
  QrCode,
  RotateCcw,
  Search,
  ServerCog,
  Star,
  Trash2,
  Zap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { buildHostUrl } from "@/lib/api/client";
import { withLoopbackHint } from "@/lib/http/loopback";
import { getTelegramBotUrl } from "@/lib/api/broker-client";
import type { HostProfile } from "@/lib/api/types";
import {
  BROWSE_HOST_CHANGE_EVENT,
  LOCAL_HOST,
  LOCAL_HOST_ID,
  checkHostCompatibility,
  clearSelectedHostId,
  deleteHostProfile,
  getAllHostProfiles,
  getHostProfiles,
  getSelectedHostId,
  isLocalOrigin,
  replaceHostProfiles,
  saveHostProfile,
  setSelectedHostId,
  suggestBrokerFromProbeFail,
  type HostCompatibility,
} from "@/lib/host-profiles";
import { probeLocalBootstrap, resetLocalBootstrapCache } from "@/lib/hosts/local-bootstrap";
import { QrPairingPanel } from "@/components/hosts/qr-pairing-panel";
import { cn } from "@/lib/utils";

const CLI_KIND_OPTIONS = [
  { value: "copilot", label: "GitHub Copilot CLI" },
  { value: "claude", label: "Claude" },
  { value: "other", label: "Other" },
];

/**
 * Full host management surface: list all profiles, add, remove, set-default,
 * and restore local/default behavior.
 *
 * Used by the Settings "Hosts & connections" card. Works as an uncontrolled
 * component — reads/writes localStorage directly via host-profiles helpers
 * and dispatches BROWSE_HOST_CHANGE_EVENT so the HostProvider reacts.
 */
/** Returns the current control-plane origin when not running on localhost. */
function getHostedOrigin(): string | null {
  if (typeof window === "undefined") return null;
  // E2E test escape hatch — allows cross-browser origin mocking without redefining
  // window.location, which is non-configurable in real browsers (Edge, mobile Chrome).
  // Set window.__E2E_HOSTED_ORIGIN__ via page.addInitScript() to simulate a hosted origin.
  const testOrigin = (window as Window & { __E2E_HOSTED_ORIGIN__?: string }).__E2E_HOSTED_ORIGIN__;
  if (testOrigin) return testOrigin;
  const origin = window.location.origin;
  if (isLocalOrigin(origin)) return null;
  return origin;
}

/**
 * Probes a remote host's operator capabilities endpoint from the browser context.
 * Using an authenticated /api/* route exercises the real CORS + auth path for
 * hosted control planes instead of the unauthenticated /healthz shortcut.
 * Returns null on success, or an actionable error string on failure.
 */
async function probeRemoteHost(baseUrl: string, token: string): Promise<string | null> {
  const origin = typeof window !== "undefined" ? window.location.origin : "this origin";
  try {
    const probeUrl = buildHostUrl(baseUrl, "/api/operator/capabilities");
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const probeUrlString = probeUrl.toString();
    const res = await fetch(
      probeUrlString,
      withLoopbackHint(probeUrlString, {
        method: "GET",
        headers,
        signal: AbortSignal.timeout(8000),
      })
    );
    if (res.status === 401) {
      return "Authentication failed (401) — the token was rejected. Check the auth token.";
    }
    if (res.status === 403) {
      return `Forbidden (403) — check the token and that ${origin} is in the operator host CORS allowlist.`;
    }
    if (!res.ok) {
      return `Host returned HTTP ${res.status}. Verify the tunnel URL and token.`;
    }
    return null;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      return "Connection timed out (8 s). Check that the tunnel URL is reachable from your browser.";
    }
    return (
      `Could not reach the host. Possible causes: tunnel not running, wrong URL, or ${origin} is not in the operator host CORS allowlist. ` +
      "Check the operator host and try again."
    );
  }
}

export function HostManagement({ className, ...props }: ComponentProps<"div">) {
  const [allHosts, setAllHosts] = useState<HostProfile[]>([LOCAL_HOST]);
  const [selectedId, setSelectedIdLocal] = useState<string | null>(null);
  const [addingNew, setAddingNew] = useState(false);
  const [newUrl, setNewUrl] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newToken, setNewToken] = useState("");
  const [newCliKind, setNewCliKind] = useState("copilot");
  const [validating, setValidating] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  /** True when the validation error is a deterministic compatibility failure (no "Save anyway"). */
  const [isCompatibilityError, setIsCompatibilityError] = useState(false);
  /** Informational note when PNA is required — not a blocking error. */
  const [pnaNote, setPnaNote] = useState<string | null>(null);
  const [originCopied, setOriginCopied] = useState(false);
  /** True when the QR / browse:// pairing panel is open (#58). */
  const [showQrPanel, setShowQrPanel] = useState(false);
  /** Demo mode badge text from the most recent local backend probe (#59). */
  const [localDemoModeBadge, setLocalDemoModeBadge] = useState<string | null>(null);

  // ── Broker mode state (#65) ─────────────────────────────────────────────────
  /** Connectivity mode for the new host being added. */
  const [newConnectivityMode, setNewConnectivityMode] = useState<"direct" | "broker">("direct");
  /** Telegram bot name for broker mode setup. */
  const [newBotName, setNewBotName] = useState("");
  /** Pairing token for broker mode setup. */
  const [newPairingToken, setNewPairingToken] = useState("");
  /**
   * Diagnosis result from a failed probe. When `broker-required`, the Broker Mode
   * tab becomes visible and a recommendation banner is shown. Null when no diagnosis
   * has been run or the most recent probe succeeded (#65 diagnosis-driven display).
   */
  const [brokerDiagnosis, setBrokerDiagnosis] = useState<HostCompatibility | null>(null);

  // ── Detect local backend state ──────────────────────────────────────────────
  type DetectStatus = "idle" | "probing" | "detected" | "auth-required" | "unavailable";
  const [detectStatus, setDetectStatus] = useState<DetectStatus>("idle");
  const [detectedUrl, setDetectedUrl] = useState<string | null>(null);

  const [hostedOrigin, setHostedOrigin] = useState<string | null>(null);

  // ── Telemetry consent state (#558) ─────────────────────────────────────────
  /**
   * When non-null, the operator is about to enable telemetry on this host for
   * the first time. The first-time consent banner / dialog requires explicit
   * acknowledgement before `telemetry_enabled` flips to `true`.
   */
  const [telemetryConsentHostId, setTelemetryConsentHostId] = useState<string | null>(null);
  // Defer hosted-origin detection to after mount to avoid SSR/hydration mismatch.
  // getHostedOrigin() reads window.location.origin which is unavailable on the server;
  // calling it synchronously during render causes React hydration error #418 when the
  // SSR HTML (hostedOrigin=null) doesn't match the client render (hostedOrigin set).
  useEffect(() => {
    setHostedOrigin(getHostedOrigin());
  }, []);

  function refresh() {
    setAllHosts(getAllHostProfiles());
    setSelectedIdLocal(getSelectedHostId());
  }

  useEffect(() => {
    refresh();
    window.addEventListener("storage", refresh);
    window.addEventListener(BROWSE_HOST_CHANGE_EVENT, refresh);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener(BROWSE_HOST_CHANGE_EVENT, refresh);
    };
  }, []);

  async function handleAdd() {
    const url = newUrl.trim();
    if (!url) return;

    // Schema-level URL validation — must be a valid absolute URL with http(s) scheme.
    // This runs before the browser compatibility check and network probe.
    try {
      const { protocol } = new URL(url);
      if (protocol !== "http:" && protocol !== "https:") {
        setValidationError(
          `URL scheme "${protocol.replace(":", "")}" is not allowed. Use https:// (or http:// for local tunnels).`
        );
        setIsCompatibilityError(true);
        return;
      }
    } catch {
      setValidationError(
        "Enter a valid URL including the scheme (e.g. https://your-tunnel.ngrok.io)."
      );
      setIsCompatibilityError(true);
      return;
    }

    // Browser compatibility check — probe-required for HTTPS → HTTP loopback.
    if (typeof window !== "undefined") {
      const compat = checkHostCompatibility(window.location.origin, {
        id: "temp",
        label: url,
        base_url: url,
        token: newToken.trim(),
        cli_kind: newCliKind,
        is_default: false,
      });
      if (compat.code === "pna-required") {
        // Browser-dependent: not a hard block. Show an informational note and
        // proceed to probe. Chromium/Edge may succeed; Safari/Firefox may not.
        setPnaNote(compat.reason);
      } else if (!compat.compatible) {
        setValidationError(compat.reason);
        setIsCompatibilityError(true);
        setPnaNote(null);
        return;
      } else {
        setPnaNote(null);
      }
    }

    setValidating(true);
    setValidationError(null);
    setIsCompatibilityError(false);

    const error = await probeRemoteHost(url, newToken.trim());
    if (error) {
      setValidationError(error);
      setValidating(false);
      // Diagnosis-driven broker recommendation (#65): after a non-auth probe
      // failure the network may be tunnel-hostile. Surface broker relay mode as
      // an option (shows banner + tab) but DO NOT auto-switch — the operator
      // must still see the validation error and can decide whether to use broker
      // mode by clicking the newly-visible "Broker Mode" tab.
      const isAuthError =
        error.includes("Authentication failed (401)") || error.includes("Forbidden (403)");
      const suggestion = suggestBrokerFromProbeFail(url, isAuthError);
      setBrokerDiagnosis(suggestion);
      return;
    }

    // Probe succeeded — clear any previous broker diagnosis.
    setBrokerDiagnosis(null);

    const profile: HostProfile = {
      id: `host-${Date.now()}`,
      label: newLabel.trim() || url,
      base_url: url,
      token: newToken.trim(),
      cli_kind: newCliKind,
      is_default: false,
      connectivity_mode: newConnectivityMode === "broker" ? "broker" : "tunnel",
    };
    saveHostProfile(profile);
    setSelectedHostId(profile.id);
    setAddingNew(false);
    setNewUrl("");
    setNewLabel("");
    setNewToken("");
    setNewCliKind("copilot");
    setNewConnectivityMode("direct");
    setNewBotName("");
    setNewPairingToken("");
    setPnaNote(null);
    setBrokerDiagnosis(null);
    setValidating(false);
    setValidationError(null);
    refresh();
  }

  /** Saves the host profile without a browser-context probe (operator escape hatch). */
  function handleAddSkipValidation() {
    const url = newUrl.trim();
    if (!url) return;
    const profile: HostProfile = {
      id: `host-${Date.now()}`,
      label: newLabel.trim() || url,
      base_url: url,
      token: newToken.trim(),
      cli_kind: newCliKind,
      is_default: false,
      connectivity_mode: "tunnel",
    };
    saveHostProfile(profile);
    setSelectedHostId(profile.id);
    setAddingNew(false);
    setNewUrl("");
    setNewLabel("");
    setNewToken("");
    setNewCliKind("copilot");
    setNewConnectivityMode("direct");
    setNewBotName("");
    setNewPairingToken("");
    setPnaNote(null);
    setBrokerDiagnosis(null);
    setValidationError(null);
    refresh();
  }

  /**
   * Saves a broker-mode host profile (#65).
   * The base_url is constructed from the bot name and pairing token.
   * No browser connectivity probe is performed — broker calls never hit the URL directly.
   */
  function handleAddBroker() {
    const botName = newBotName.trim();
    if (!botName) return;
    const pairingToken = newPairingToken.trim();
    // Build a valid HTTPS URL that identifies the broker endpoint.
    // `getTelegramBotUrl` produces `https://t.me/<botName>?start=<token>` (valid HTTPS).
    const brokerUrl = pairingToken
      ? getTelegramBotUrl(botName, pairingToken)
      : `https://t.me/${encodeURIComponent(botName)}`;
    const profile: HostProfile = {
      id: `host-${Date.now()}`,
      label: newLabel.trim() || `@${botName} (Telegram broker)`,
      base_url: brokerUrl,
      token: pairingToken,
      cli_kind: newCliKind,
      is_default: false,
      connectivity_mode: "broker",
    };
    saveHostProfile(profile);
    setSelectedHostId(profile.id);
    setAddingNew(false);
    setNewUrl("");
    setNewLabel("");
    setNewToken("");
    setNewCliKind("copilot");
    setNewConnectivityMode("direct");
    setNewBotName("");
    setNewPairingToken("");
    setPnaNote(null);
    setBrokerDiagnosis(null);
    setValidationError(null);
    refresh();
  }

  function handleCopyOrigin() {
    if (!hostedOrigin) return;
    void navigator.clipboard.writeText(hostedOrigin).then(() => {
      setOriginCopied(true);
      setTimeout(() => setOriginCopied(false), 1500);
    });
  }

  async function handleDetectLocal() {
    setDetectStatus("probing");
    setDetectedUrl(null);
    setLocalDemoModeBadge(null);
    resetLocalBootstrapCache();
    const result = await probeLocalBootstrap();
    if (result.status === "detected") {
      setDetectStatus("detected");
      setDetectedUrl(result.url);
      setLocalDemoModeBadge(result.response.demo_mode_badge ?? null);
    } else if (result.status === "auth-required") {
      setDetectStatus("auth-required");
      setDetectedUrl(result.url);
      setLocalDemoModeBadge(result.response.demo_mode_badge ?? null);
      // Pre-fill the add-host form with the detected URL.
      setAddingNew(true);
      setNewUrl(result.url);
    } else {
      setDetectStatus("unavailable");
    }
  }

  function handleAddDetected() {
    if (!detectedUrl) return;
    const profile: HostProfile = {
      id: `host-${Date.now()}`,
      label: localDemoModeBadge
        ? `Local backend (auto-detected) · ${localDemoModeBadge}`
        : "Local backend (auto-detected)",
      base_url: detectedUrl,
      token: "",
      cli_kind: "copilot",
      is_default: false,
      // Persist the demo-mode badge so the host row shows a stable "Demo mode"
      // badge after the profile is saved (#59 stable-badge requirement).
      demo_mode_badge: localDemoModeBadge ?? undefined,
    };
    saveHostProfile(profile);
    setSelectedHostId(profile.id);
    setDetectStatus("idle");
    setDetectedUrl(null);
    refresh();
  }

  /**
   * Called by QrPairingPanel after a successful ticket decode (#58).
   * Pre-fills the add-host form with the discovered base URL.
   */
  function handleQrPaired(baseUrl: string, isOpenAuth: boolean) {
    setShowQrPanel(false);
    setAddingNew(true);
    setNewUrl(baseUrl);
    setNewLabel("");
    setNewToken("");
    setNewCliKind("copilot");
    setPnaNote(null);
    setValidationError(null);
    setIsCompatibilityError(false);
    if (isOpenAuth) {
      // Open-auth backend — no token needed; show informational note.
      setPnaNote("Open-auth backend detected via QR pairing — no token required.");
    }
  }

  function handleRemove(id: string) {
    deleteHostProfile(id);
    refresh();
  }

  function handleSetDefault(id: string) {
    const profiles = getHostProfiles();
    replaceHostProfiles(profiles.map((p) => ({ ...p, is_default: p.id === id })));
    setSelectedHostId(id);
    refresh();
  }

  function handleClearDefault() {
    const profiles = getHostProfiles();
    replaceHostProfiles(profiles.map((p) => ({ ...p, is_default: false })));
    clearSelectedHostId();
    refresh();
  }

  /**
   * #558: Toggle the opt-in `telemetry_enabled` flag on a host profile.
   *
   * - When turning OFF, persist immediately (no consent needed to stop polling).
   * - When turning ON for the first time (consent not yet acknowledged for this
   *   host), open the first-time consent dialog. The actual flip happens in
   *   `handleTelemetryConsentConfirm` after the operator clicks "Enable".
   * - When turning ON for a host that has already acknowledged consent (e.g.
   *   the user previously disabled it), persist immediately.
   */
  function handleToggleTelemetry(host: HostProfile) {
    if (host.telemetry_enabled) {
      saveHostProfile({ ...host, telemetry_enabled: false });
      refresh();
      return;
    }
    if (host.telemetry_consent_acked) {
      saveHostProfile({ ...host, telemetry_enabled: true });
      refresh();
      return;
    }
    setTelemetryConsentHostId(host.id);
  }

  function handleTelemetryConsentConfirm(id: string) {
    const profile = allHosts.find((p) => p.id === id);
    if (!profile) {
      setTelemetryConsentHostId(null);
      return;
    }
    saveHostProfile({
      ...profile,
      telemetry_enabled: true,
      telemetry_consent_acked: true,
    });
    setTelemetryConsentHostId(null);
    refresh();
  }

  const activeId = selectedId ?? allHosts.find((h) => h.is_default)?.id ?? LOCAL_HOST_ID;
  const remoteHosts = allHosts.filter((h) => h.id !== LOCAL_HOST_ID);

  return (
    <div className={cn("space-y-4", className)} {...props}>
      {/* Hosted control-plane origin — shown when not on localhost (issue #30) */}
      {hostedOrigin ? (
        <div
          className="flex items-center justify-between gap-2 rounded-lg border border-dashed px-3 py-2 text-xs"
          data-testid="hosted-origin-strip"
        >
          <div className="min-w-0">
            <p className="text-muted-foreground">Control-plane origin</p>
            <p className="text-foreground truncate font-mono font-medium">{hostedOrigin}</p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 shrink-0"
            onClick={handleCopyOrigin}
            aria-label="Copy control-plane origin"
            data-testid="copy-origin-btn"
          >
            {originCopied ? (
              <Check className="size-3.5 text-emerald-500" />
            ) : (
              <Copy className="size-3.5" />
            )}
          </Button>
        </div>
      ) : null}
      {/* Host list */}
      <div className="space-y-2">
        {/* LOCAL_HOST row */}
        <div
          className={cn(
            "flex items-center justify-between rounded-lg border px-3 py-2 text-sm",
            activeId === LOCAL_HOST_ID && "border-primary bg-primary/5"
          )}
          data-testid="host-row-local"
        >
          <div className="flex min-w-0 items-center gap-2">
            <ServerCog className="text-muted-foreground size-4 shrink-0" />
            <div className="min-w-0">
              <p className="font-medium">{LOCAL_HOST.label}</p>
              <p className="text-muted-foreground text-xs">
                {hostedOrigin ? "Open the local browse app directly" : "Same-origin default"}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {activeId === LOCAL_HOST_ID && (
              <CheckCircle2
                className="size-4 text-emerald-500"
                aria-label="Active"
                data-testid="host-active-indicator"
              />
            )}
          </div>
        </div>

        {/* Remote host rows */}
        {remoteHosts.map((host) => (
          <div
            key={host.id}
            className={cn(
              "flex items-center justify-between rounded-lg border px-3 py-2 text-sm",
              activeId === host.id && "border-primary bg-primary/5"
            )}
            data-testid={`host-row-${host.id}`}
          >
            <div className="flex min-w-0 items-center gap-2">
              <Globe className="text-muted-foreground size-4 shrink-0" />
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="truncate font-medium">{host.label}</p>
                  {host.is_default && (
                    <span
                      className="bg-primary/10 text-primary shrink-0 rounded px-1 py-0.5 text-[10px] font-medium tracking-wide uppercase"
                      data-testid={`host-default-badge-${host.id}`}
                    >
                      default
                    </span>
                  )}
                  {host.demo_mode_badge && (
                    <span
                      className="shrink-0 rounded bg-amber-500/10 px-1 py-0.5 text-[10px] font-medium tracking-wide text-amber-700 uppercase dark:text-amber-300"
                      data-testid={`host-demo-badge-${host.id}`}
                      title="This host was added from a static/demo pairing slot (read-only, separate audit log)"
                    >
                      demo
                    </span>
                  )}
                  {host.connectivity_mode === "broker" && (
                    <span
                      className="shrink-0 rounded bg-purple-500/10 px-1 py-0.5 text-[10px] font-medium tracking-wide text-purple-700 uppercase dark:text-purple-300"
                      data-testid={`host-broker-badge-${host.id}`}
                      title="This host uses broker relay mode — traffic routes through the relay, not directly from your browser"
                    >
                      broker
                    </span>
                  )}
                </div>
                <p className="text-muted-foreground max-w-[200px] truncate font-mono text-xs">
                  {host.base_url.replace(/^https?:\/\//, "")}
                </p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {activeId === host.id && (
                <CheckCircle2
                  className="size-4 text-emerald-500"
                  aria-label="Active"
                  data-testid="host-active-indicator"
                />
              )}
              {activeId !== host.id && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => setSelectedHostId(host.id)}
                  aria-label={`Switch to ${host.label}`}
                >
                  Switch
                </Button>
              )}
              {!host.is_default && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  onClick={() => handleSetDefault(host.id)}
                  aria-label={`Set ${host.label} as default`}
                  title="Set as default host"
                >
                  <Star className="size-3.5" />
                </Button>
              )}
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className={cn(
                  "size-7",
                  host.telemetry_enabled
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-muted-foreground"
                )}
                onClick={() => handleToggleTelemetry(host)}
                aria-label={
                  host.telemetry_enabled
                    ? `Disable host telemetry for ${host.label}`
                    : `Enable host telemetry for ${host.label}`
                }
                aria-pressed={host.telemetry_enabled === true}
                title={
                  host.telemetry_enabled
                    ? "Host telemetry enabled — aggregate CPU/RAM/network/filesystem. Click to disable."
                    : "Enable opt-in aggregate host telemetry (CPU/RAM/network/filesystem). No command lines, env vars, prompts, paths, or tokens are sent."
                }
                data-testid={`host-telemetry-toggle-${host.id}`}
              >
                <Activity className="size-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="text-destructive/70 hover:text-destructive size-7"
                onClick={() => handleRemove(host.id)}
                aria-label={`Remove host ${host.label}`}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>
          </div>
        ))}

        {remoteHosts.length === 0 && (
          <p
            className="text-muted-foreground rounded-lg border border-dashed px-3 py-4 text-center text-sm"
            data-testid="no-remote-hosts"
          >
            No remote hosts saved. Add a public tunnel URL below to connect to a remote CLI agent.
          </p>
        )}

        {/* #558: First-time telemetry consent banner */}
        {telemetryConsentHostId !== null && (
          <div
            className="rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm"
            role="dialog"
            aria-modal="false"
            aria-labelledby="telemetry-consent-title"
            data-testid="telemetry-consent-banner"
          >
            <p
              id="telemetry-consent-title"
              className="mb-1 font-medium text-amber-900 dark:text-amber-200"
            >
              Enable host telemetry?
            </p>
            <p className="text-muted-foreground mb-2">
              The UI will poll <code className="font-mono">/api/operator/host/metrics</code> on this
              host every 5 seconds and display aggregate CPU load, memory %, network counter totals,
              and coarse filesystem usage (root / home / data). Sampling is capped at 1 Hz by the
              backend.
            </p>
            <p className="text-muted-foreground mb-3">
              <strong>Never sent:</strong> command lines, environment variables, prompt text,
              tokens, absolute file paths, or network interface names.
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                onClick={() => handleTelemetryConsentConfirm(telemetryConsentHostId)}
                data-testid="telemetry-consent-confirm"
              >
                Enable telemetry
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setTelemetryConsentHostId(null)}
                data-testid="telemetry-consent-cancel"
              >
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Actions bar */}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            setAddingNew((v) => !v);
            if (showQrPanel) setShowQrPanel(false);
          }}
          aria-label="Add remote host"
          aria-expanded={addingNew}
        >
          <Plus className="mr-1.5 size-3.5" />
          Add host
        </Button>
        {/* QR pairing button — shown on hosted origins where tunnels are common (#58) */}
        {hostedOrigin ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setShowQrPanel((v) => !v);
              if (addingNew) setAddingNew(false);
            }}
            aria-label="Pair via QR code"
            aria-expanded={showQrPanel}
            data-testid="qr-pairing-btn"
          >
            <QrCode className="mr-1.5 size-3.5" />
            Pair via QR
          </Button>
        ) : null}
        {hostedOrigin ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void handleDetectLocal()}
            disabled={detectStatus === "probing"}
            data-testid="detect-local-btn"
            aria-label="Detect local backend"
          >
            {detectStatus === "probing" ? (
              <>
                <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                Detecting…
              </>
            ) : (
              <>
                <Search className="mr-1.5 size-3.5" />
                Detect local backend
              </>
            )}
          </Button>
        ) : null}
        {activeId !== LOCAL_HOST_ID && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleClearDefault}
            data-testid="restore-local-btn"
          >
            <RotateCcw className="mr-1.5 size-3.5" />
            Restore local
          </Button>
        )}
      </div>

      {/* QR / browse:// pairing panel (#58) */}
      {showQrPanel && hostedOrigin ? (
        <QrPairingPanel
          onPaired={handleQrPaired}
          onCancel={() => setShowQrPanel(false)}
          data-testid="qr-pairing-panel-wrapper"
        />
      ) : null}

      {/* Demo mode banner — shown when the local backend probe returned static_mode_active=true (#59) */}
      {localDemoModeBadge ? (
        <div
          className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs"
          data-testid="demo-mode-banner"
          role="status"
        >
          <Zap className="size-3.5 shrink-0 text-amber-500" />
          <p className="font-medium text-amber-700 dark:text-amber-300">
            {localDemoModeBadge} active on the detected backend — connections via the static slot
            are read-only and appear in a separate audit log.
          </p>
        </div>
      ) : null}

      {/* Detect local backend result */}
      {detectStatus === "detected" && detectedUrl ? (
        <div
          className="flex items-start justify-between gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-xs"
          data-testid="detect-result-detected"
        >
          <div className="flex items-start gap-2">
            <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-500" />
            <p>
              Local backend detected at <span className="font-mono font-medium">{detectedUrl}</span>
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            className="h-6 shrink-0 px-2 text-xs"
            onClick={handleAddDetected}
            data-testid="add-detected-btn"
          >
            Add &amp; use
          </Button>
        </div>
      ) : null}
      {detectStatus === "auth-required" && detectedUrl ? (
        <div
          className="flex items-start gap-2 rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-3 py-2 text-xs"
          data-testid="detect-result-auth-required"
        >
          <Info className="mt-0.5 size-3.5 shrink-0 text-yellow-500" />
          <p>
            Backend at <span className="font-mono font-medium">{detectedUrl}</span> requires a token
            — enter it in the form below.
          </p>
        </div>
      ) : null}
      {detectStatus === "unavailable" ? (
        <div
          className="flex items-start gap-2 rounded-lg border border-dashed px-3 py-2 text-xs"
          data-testid="detect-result-unavailable"
        >
          <AlertCircle className="text-muted-foreground mt-0.5 size-3.5 shrink-0" />
          <p className="text-muted-foreground">
            No local backend found at 127.0.0.1:8765 or localhost:8765.{" "}
            <a
              href="http://127.0.0.1:8765/"
              target="_blank"
              rel="noreferrer"
              className="text-foreground underline underline-offset-2"
              data-testid="detect-result-open-local-ui"
            >
              Open local app directly
            </a>
            .
          </p>
        </div>
      ) : null}

      {/* Add host form */}
      {addingNew && (
        <div className="space-y-3 rounded-lg border p-4" data-testid="host-add-form">
          {/* Broker relay mode recommendation banner — shown when a probe failure
              has diagnosed the network as tunnel-hostile (#65 diagnosis-driven). */}
          {brokerDiagnosis?.code === "broker-required" ? (
            <div
              className="flex items-start gap-2 rounded-lg border border-purple-500/30 bg-purple-500/5 px-3 py-2 text-xs"
              data-testid="broker-recommendation-banner"
              role="status"
            >
              <Network className="mt-0.5 size-3.5 shrink-0 text-purple-500" />
              <p className="text-purple-700 dark:text-purple-300">
                <span className="font-medium">Broker relay mode recommended.</span> Direct
                connection failed — your network may block browser→backend connections. Configure
                broker relay mode below, or{" "}
                <button
                  type="button"
                  className="underline"
                  onClick={() => {
                    setBrokerDiagnosis(null);
                    setNewConnectivityMode("direct");
                  }}
                >
                  try direct again
                </button>
                .
              </p>
            </div>
          ) : null}

          {/* Connectivity mode tab selector (#65):
              - Always shows "Tunnel / Direct" tab.
              - "Broker Mode" tab is shown ONLY when a probe diagnosis has returned
                broker-required (diagnosis-driven, not unconditional). */}
          <div
            className="bg-muted flex rounded-md p-0.5 text-xs"
            role="tablist"
            aria-label="Connection type"
            data-testid="connectivity-mode-tabs"
          >
            <button
              type="button"
              role="tab"
              aria-selected={newConnectivityMode === "direct"}
              onClick={() => {
                setNewConnectivityMode("direct");
                setValidationError(null);
                setIsCompatibilityError(false);
              }}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded px-2 py-1 text-xs font-medium transition-colors",
                newConnectivityMode === "direct"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
              data-testid="tab-direct"
            >
              <Globe className="size-3" />
              Tunnel / Direct
            </button>
            {/* Broker Mode tab — only shown when diagnosis has determined
                broker relay is required (tunnel-hostile probe failure). */}
            {brokerDiagnosis?.code === "broker-required" ? (
              <button
                type="button"
                role="tab"
                aria-selected={newConnectivityMode === "broker"}
                onClick={() => {
                  setNewConnectivityMode("broker");
                  setValidationError(null);
                  setIsCompatibilityError(false);
                  setPnaNote(null);
                }}
                className={cn(
                  "flex flex-1 items-center justify-center gap-1.5 rounded px-2 py-1 text-xs font-medium transition-colors",
                  newConnectivityMode === "broker"
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
                data-testid="tab-broker"
              >
                <Network className="size-3" />
                Broker Mode
              </button>
            ) : null}
          </div>

          {/* Direct / Tunnel form */}
          {newConnectivityMode === "direct" && (
            <>
              <p className="text-muted-foreground text-xs">
                Add a public tunnel URL (e.g. ngrok, Cloudflare Tunnel, VS Code forwarded port) that
                exposes the Copilot CLI operator API.
              </p>
              <input
                type="url"
                value={newUrl}
                onChange={(e) => {
                  setNewUrl(e.target.value);
                  setPnaNote(null);
                  // Changing the URL invalidates the previous probe diagnosis.
                  // (We're inside the direct form block so mode is already "direct".)
                  setBrokerDiagnosis(null);
                  if (validationError) {
                    setValidationError(null);
                    setIsCompatibilityError(false);
                  }
                }}
                placeholder="https://abc123.ngrok.io"
                aria-label="Tunnel URL"
                className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-1.5 font-mono text-xs outline-none focus-visible:ring-2"
              />
              <div className="grid grid-cols-2 gap-2">
                <input
                  type="text"
                  value={newLabel}
                  onChange={(e) => setNewLabel(e.target.value)}
                  placeholder="Label (optional)"
                  aria-label="Host label"
                  className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-1.5 text-xs outline-none focus-visible:ring-2"
                />
                <input
                  type="password"
                  value={newToken}
                  onChange={(e) => setNewToken(e.target.value)}
                  placeholder="Auth token (optional)"
                  aria-label="Auth token"
                  className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-1.5 text-xs outline-none focus-visible:ring-2"
                />
              </div>
              <Select
                value={newCliKind}
                onValueChange={(v) => {
                  if (v) setNewCliKind(v);
                }}
              >
                <SelectTrigger className="h-8 text-xs" aria-label="CLI kind">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CLI_KIND_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value} className="text-xs">
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </>
          )}

          {/* Broker Mode form (#65) */}
          {newConnectivityMode === "broker" && (
            <div className="space-y-3" data-testid="broker-mode-form">
              {/* Setup instructions */}
              <div
                className="space-y-1.5 rounded-lg border border-blue-500/30 bg-blue-500/5 px-3 py-2 text-xs"
                data-testid="broker-instructions"
              >
                <p className="font-medium text-blue-700 dark:text-blue-300">
                  Broker mode setup (Telegram)
                </p>
                <ol className="text-muted-foreground list-decimal space-y-1 pl-4">
                  <li>
                    On your machine, run:{" "}
                    <code className="bg-muted rounded px-1">
                      python browse.py --broker-mode telegram
                    </code>
                  </li>
                  <li>Note the bot name and pairing token printed to the console.</li>
                  <li>Enter them below, then tap the deep-link to open your bot on your phone.</li>
                  <li>
                    Verify: send <code className="bg-muted rounded px-1">/status</code> to your bot.
                    If you receive a reply, the broker is running.
                  </li>
                </ol>
                <p className="text-muted-foreground pt-1">
                  See{" "}
                  <a
                    href="/docs/HOSTED-SHELL-ARCHITECTURE.md#54-frontend-probe-logic--when-to-suggest-broker-mode"
                    className="text-blue-600 underline dark:text-blue-400"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    HOSTED-SHELL-ARCHITECTURE.md §5.4
                  </a>{" "}
                  for full setup guidance.
                </p>
              </div>

              {/* Bot name */}
              <input
                type="text"
                value={newBotName}
                onChange={(e) => setNewBotName(e.target.value)}
                placeholder="Telegram bot name (e.g. my_copilot_bot)"
                aria-label="Telegram bot name"
                className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-1.5 font-mono text-xs outline-none focus-visible:ring-2"
                data-testid="broker-bot-name"
              />

              <div className="grid grid-cols-2 gap-2">
                {/* Label */}
                <input
                  type="text"
                  value={newLabel}
                  onChange={(e) => setNewLabel(e.target.value)}
                  placeholder="Label (optional)"
                  aria-label="Host label"
                  className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-1.5 text-xs outline-none focus-visible:ring-2"
                />
                {/* Pairing token */}
                <input
                  type="password"
                  value={newPairingToken}
                  onChange={(e) => setNewPairingToken(e.target.value)}
                  placeholder="Pairing token (from console)"
                  aria-label="Broker pairing token"
                  className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-1.5 text-xs outline-none focus-visible:ring-2"
                  data-testid="broker-pairing-token"
                />
              </div>

              {/* Deep-link preview */}
              {newBotName.trim() ? (
                <div className="rounded-lg border border-dashed px-3 py-2 text-xs">
                  <p className="text-muted-foreground mb-1 font-medium">Bot deep-link</p>
                  <a
                    href={
                      newPairingToken.trim()
                        ? getTelegramBotUrl(newBotName.trim(), newPairingToken.trim())
                        : `https://t.me/${encodeURIComponent(newBotName.trim())}`
                    }
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary font-mono break-all underline"
                    data-testid="broker-deep-link"
                  >
                    {newPairingToken.trim()
                      ? getTelegramBotUrl(newBotName.trim(), newPairingToken.trim())
                      : `https://t.me/${encodeURIComponent(newBotName.trim())}`}
                  </a>
                </div>
              ) : null}
            </div>
          )}

          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={() => {
                setAddingNew(false);
                setNewUrl("");
                setNewLabel("");
                setNewToken("");
                setNewCliKind("copilot");
                setNewConnectivityMode("direct");
                setNewBotName("");
                setNewPairingToken("");
                setPnaNote(null);
                setBrokerDiagnosis(null);
                setValidationError(null);
                setIsCompatibilityError(false);
              }}
            >
              Cancel
            </Button>
            {/* Save anyway — only for direct/tunnel mode with non-deterministic errors */}
            {newConnectivityMode === "direct" && validationError && !isCompatibilityError ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={handleAddSkipValidation}
                disabled={!newUrl.trim()}
                data-testid="skip-validation-btn"
                title="Save without browser-context validation"
              >
                Save anyway
              </Button>
            ) : null}
            {/* Save button — direct/tunnel vs broker paths */}
            {newConnectivityMode === "broker" ? (
              <Button
                type="button"
                size="sm"
                className="h-7 text-xs"
                onClick={handleAddBroker}
                disabled={!newBotName.trim()}
                data-testid="save-broker-btn"
              >
                Save broker
              </Button>
            ) : (
              <Button
                type="button"
                size="sm"
                className="h-7 text-xs"
                onClick={() => void handleAdd()}
                disabled={!newUrl.trim() || validating}
                data-testid="save-host-btn"
              >
                {validating ? (
                  <>
                    <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                    Validating…
                  </>
                ) : (
                  "Save host"
                )}
              </Button>
            )}
          </div>
          {pnaNote && newConnectivityMode === "direct" ? (
            <div
              className="flex items-start gap-2 rounded-lg border border-blue-500/30 bg-blue-500/5 px-3 py-2 text-xs"
              data-testid="pna-note"
            >
              <Info className="mt-0.5 size-3.5 shrink-0 text-blue-500" />
              <p className="text-blue-700 dark:text-blue-300">{pnaNote}</p>
            </div>
          ) : null}
          {validationError ? (
            <div
              className="border-destructive/30 bg-destructive/5 text-destructive flex items-start gap-2 rounded-lg border px-3 py-2 text-xs"
              data-testid="validation-error"
              role="alert"
            >
              <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
              <p>{validationError}</p>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
