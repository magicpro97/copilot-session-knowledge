"use client";

import { AlertCircle, Info } from "lucide-react";
import type { HostCompatibility } from "@/lib/host-profiles";
import type { LocalBootstrapResult } from "@/lib/hosts/local-bootstrap";

export interface DiagnosticPanelProps {
  compat: HostCompatibility | null;
  probeResult?: LocalBootstrapResult | null;
  isHosted: boolean;
  hostUrl: string;
}

/**
 * Renders a diagnostic/troubleshooting banner when the selected host is not
 * reachable or has compatibility issues.
 *
 * Shown inline in SessionCreateDialog when `hostReady` is false.
 */
export function DiagnosticPanel({ compat, probeResult, isHosted, hostUrl }: DiagnosticPanelProps) {
  const messages: string[] = [];

  if (compat && !compat.compatible) {
    messages.push(compat.reason ?? `Host "${hostUrl}" is not compatible with this origin.`);
  }

  if (probeResult) {
    if (probeResult.status === "unavailable") {
      messages.push(
        "Local backend not detected — make sure the CLI is running with `--browse` enabled.",
      );
    } else if (probeResult.status === "auth-required") {
      messages.push("Backend requires authentication — add a token in the host settings.");
    } else if (probeResult.status === "cached-negative") {
      messages.push("Local backend probe is cached as unavailable. Retry in a few minutes.");
    }
  }

  if (messages.length === 0 && isHosted) {
    messages.push(
      "Cannot reach the selected host from this origin. Check the URL and CORS settings.",
    );
  }

  if (messages.length === 0) return null;

  const Icon = compat && !compat.compatible ? AlertCircle : Info;

  return (
    <div
      className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs"
      role="status"
      data-testid="diagnostic-panel"
    >
      <Icon className="mt-0.5 size-3.5 shrink-0 text-amber-500" />
      <div className="space-y-1">
        {messages.map((msg, i) => (
          <p key={i} className="text-amber-700 dark:text-amber-300">
            {msg}
          </p>
        ))}
      </div>
    </div>
  );
}
