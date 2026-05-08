"use client";

/**
 * /diagnostics — standalone host connectivity diagnostic surface.
 *
 * Contains two sections:
 *
 * 1. **Live Probe** (primary, runtime-backed): probes loopback candidates on page
 *    load and renders a real DiagnosticPanel driven by the actual probe result.
 *    This is the evidence surface for issue #56 acceptance verification.
 *
 * 2. **Static Showcase** (secondary, before/after reference): preserves the old
 *    generic warning text alongside the new structured panels for SHA-256
 *    before/after screenshot comparison evidence.
 *
 * Also serves as the e2e evidence capture page for issue #56.
 */

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

import { DiagnosticPanel } from "@/components/DiagnosticPanel";
import { probeLocalBootstrap, resetLocalBootstrapCache } from "@/lib/hosts/local-bootstrap";
import type { LocalBootstrapResult, ProbeAttempt } from "@/lib/hosts/local-bootstrap";
import { checkHostCompatibility, isLocalOrigin } from "@/lib/host-profiles";

// ── Probe status badge ────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const colours: Record<string, string> = {
    detected: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
    "auth-required": "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
    unavailable: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
    "cached-negative": "bg-slate-100 text-slate-600 dark:bg-slate-800/50 dark:text-slate-400",
  };
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-medium ${colours[status] ?? colours.unavailable}`}
    >
      {status}
    </span>
  );
}

// ── Probe attempt row ─────────────────────────────────────────────────────────

function ProbeAttemptRow({ attempt }: { attempt: ProbeAttempt }) {
  return (
    <tr className="border-border border-t">
      <td className="text-muted-foreground py-1 pr-3 font-mono text-xs">{attempt.url}</td>
      <td className="py-1 pr-3">
        <span className="bg-muted text-destructive rounded px-1.5 py-0.5 font-mono text-[10px]">
          {attempt.reason}
        </span>
      </td>
      <td className="text-muted-foreground py-1 text-[10px]">{attempt.daemonState ?? "—"}</td>
    </tr>
  );
}

// ── Live probe section ────────────────────────────────────────────────────────

function LiveProbeSection() {
  const [probeResult, setProbeResult] = useState<LocalBootstrapResult | null>(null);
  const [probing, setProbing] = useState(true);
  const [origin, setOrigin] = useState<string>("");
  const [isHosted, setIsHosted] = useState<boolean>(false);
  const [hostCompat, setHostCompat] = useState<ReturnType<typeof checkHostCompatibility> | null>(
    null
  );

  const runProbe = async () => {
    setProbing(true);
    resetLocalBootstrapCache();
    const result = await probeLocalBootstrap();
    setProbeResult(result);
    setProbing(false);
  };

  useEffect(() => {
    const o = window.location.origin;
    setOrigin(o);
    setIsHosted(!isLocalOrigin(o));
    // For the diagnostics page, check compat against a representative loopback host.
    setHostCompat(
      checkHostCompatibility(o, {
        id: "diag-loopback",
        label: "loopback",
        base_url: "http://127.0.0.1:8765",
        token: "",
        cli_kind: "copilot",
        is_default: false,
      })
    );
    void runProbe();
  }, []);

  const attempts = probeResult?.status === "unavailable" ? (probeResult.reasons ?? []) : [];

  return (
    <section aria-label="Live probe results" data-testid="live-probe-section" className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Live Probe Results</h2>
        <button
          type="button"
          onClick={() => void runProbe()}
          disabled={probing}
          className="border-border text-muted-foreground hover:text-foreground inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs disabled:opacity-50"
          aria-label="Re-run probe"
          data-testid="rerun-probe-btn"
        >
          <RefreshCw className={`size-3 ${probing ? "animate-spin" : ""}`} aria-hidden="true" />
          {probing ? "Probing…" : "Re-probe"}
        </button>
      </div>

      {/* Origin info */}
      <div className="border-border bg-muted/30 rounded-md border p-2 text-xs">
        <span className="text-muted-foreground">Origin: </span>
        <code className="font-mono">{origin || "—"}</code>
        <span className="text-muted-foreground ml-2">
          ({isHosted ? "hosted static origin" : "local origin"})
        </span>
      </div>

      {/* Probe status */}
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground text-xs">Probe status:</span>
        {probing ? (
          <span className="text-muted-foreground text-xs">running…</span>
        ) : probeResult ? (
          <StatusBadge status={probeResult.status} />
        ) : null}
      </div>

      {/* Candidate attempt table (only on failure) */}
      {attempts.length > 0 && (
        <div className="overflow-x-auto">
          <table
            className="w-full text-left"
            aria-label="Probe candidate results"
            data-testid="probe-candidate-table"
          >
            <thead>
              <tr>
                <th className="text-muted-foreground pr-3 pb-1 text-[10px] font-medium tracking-wide uppercase">
                  Candidate URL
                </th>
                <th className="text-muted-foreground pr-3 pb-1 text-[10px] font-medium tracking-wide uppercase">
                  Failure reason
                </th>
                <th className="text-muted-foreground pb-1 text-[10px] font-medium tracking-wide uppercase">
                  Daemon state
                </th>
              </tr>
            </thead>
            <tbody>
              {attempts.map((a) => (
                <ProbeAttemptRow key={a.url} attempt={a} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Real DiagnosticPanel driven by live probe state */}
      {!probing && (
        <div data-testid="live-diagnostic-panel-wrapper">
          {probeResult?.status === "detected" ? (
            <p className="text-muted-foreground text-xs">
              ✓ Backend detected at{" "}
              <code className="font-mono">{"url" in probeResult ? probeResult.url : ""}</code> — no
              connectivity error.
            </p>
          ) : (
            <DiagnosticPanel
              compat={hostCompat}
              probeResult={probeResult}
              isHosted={isHosted}
              hostUrl="http://127.0.0.1:8765"
            />
          )}
        </div>
      )}
    </section>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function DiagnosticsPage() {
  return (
    <div data-testid="diagnostics-page" className="mx-auto max-w-lg space-y-8 py-8">
      <div>
        <h1 className="mb-1 text-lg font-semibold">Host Connectivity Diagnostics</h1>
        <p className="text-muted-foreground text-sm">
          Structured failure reasons and actionable fixes for browser-to-backend connectivity
          issues. Addresses{" "}
          <a
            href="https://github.com/magicpro97/copilot-session-knowledge/issues/56"
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2"
          >
            issue #56
          </a>
          .
        </p>
      </div>

      {/* ── Live probe (primary evidence section) ────────────────────────────── */}
      <LiveProbeSection />

      {/* ── Before/After showcase (static reference for SHA-256 evidence) ────── */}
      <section aria-label="Before/after reference panels" className="space-y-4">
        <h2 className="text-sm font-semibold">Before / After Reference</h2>

        {/* ── Before state: legacy generic warning ──────────────────────────── */}
        <div>
          <p className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
            Before — generic warning (issue #56)
          </p>
          <div
            data-testid="legacy-warning"
            className="rounded-lg border border-amber-500/30 bg-amber-50/50 p-3 dark:bg-amber-950/20"
          >
            <p className="text-xs text-amber-600 dark:text-amber-400">
              <strong>Cannot reach this host from a secure page.</strong> Open the local browse app
              directly, or expose your server via a public HTTPS tunnel (e.g. ngrok) and save it as
              a host.
            </p>
          </div>
        </div>

        {/* ── After: mixed-content-http DiagnosticPanel ──────────────────────── */}
        <div>
          <p className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
            After — structured diagnostic: mixed content (issue #56)
          </p>
          <DiagnosticPanel
            compat={{
              compatible: false,
              code: "mixed-content-http",
              reason:
                "A secure (HTTPS) control plane cannot connect to insecure HTTP host " +
                "http://192.168.1.10:8765. Expose the backend through HTTPS, or use loopback " +
                "with --hosted-bootstrap.",
            }}
            isHosted={true}
            hostUrl="http://192.168.1.10:8765"
          />
        </div>

        {/* ── PNA-required variant ───────────────────────────────────────────── */}
        <div aria-label="Variant — Private Network Access required">
          <p className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
            Variant — Private Network Access required
          </p>
          <DiagnosticPanel
            compat={{
              compatible: true,
              code: "pna-required",
              reason:
                "Connecting from a secure (HTTPS) origin to http://localhost:8765 requires " +
                "browser Private Network Access (PNA) support.",
            }}
            isHosted={true}
            hostUrl="http://localhost:8765"
          />
        </div>

        {/* ── Daemon not running variant ─────────────────────────────────────── */}
        <div>
          <p className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
            Variant — daemon not running
          </p>
          <DiagnosticPanel
            compat={null}
            probeResult={{
              status: "unavailable",
              reasons: [
                {
                  url: "http://127.0.0.1:8765",
                  reason: "network-error",
                  daemonState: "not-running",
                },
                {
                  url: "http://localhost:8765",
                  reason: "network-error",
                  daemonState: "not-running",
                },
              ],
            }}
            isHosted={true}
            hostUrl="http://127.0.0.1:8765"
          />
        </div>

        {/* ── Daemon running without --hosted-bootstrap variant ─────────────── */}
        <div>
          <p className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
            Variant — daemon running without --hosted-bootstrap
          </p>
          <DiagnosticPanel
            compat={null}
            probeResult={{
              status: "unavailable",
              reasons: [
                {
                  url: "http://127.0.0.1:8765",
                  reason: "network-error",
                  daemonState: "running-no-pna",
                },
                {
                  url: "http://localhost:8765",
                  reason: "network-error",
                  daemonState: "running-no-pna",
                },
              ],
            }}
            isHosted={true}
            hostUrl="http://127.0.0.1:8765"
          />
        </div>

        {/* ── No host configured variant ─────────────────────────────────────── */}
        <div aria-label="Variant — no agent host configured">
          <p className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
            Variant — no agent host configured
          </p>
          <DiagnosticPanel compat={null} isHosted={true} />
        </div>
      </section>
    </div>
  );
}
