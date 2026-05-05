"use client";

import { useEffect, useState, type ComponentProps } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Copy,
  Globe,
  Loader2,
  Plus,
  RotateCcw,
  ServerCog,
  Star,
  Trash2,
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
  replaceHostProfiles,
  saveHostProfile,
  setSelectedHostId,
} from "@/lib/host-profiles";
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
  const origin = window.location.origin;
  if (origin.includes("localhost") || origin.includes("127.0.0.1")) return null;
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
    const res = await fetch(probeUrl.toString(), {
      method: "GET",
      headers,
      signal: AbortSignal.timeout(8000),
    });
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
  const [originCopied, setOriginCopied] = useState(false);

  const hostedOrigin = getHostedOrigin();

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

    // Pre-probe compatibility check — deterministic, no network required.
    // A hosted HTTPS control plane can never reach an insecure loopback URL.
    if (typeof window !== "undefined") {
      const compat = checkHostCompatibility(window.location.origin, {
        id: "temp",
        label: url,
        base_url: url,
        token: newToken.trim(),
        cli_kind: newCliKind,
        is_default: false,
      });
      if (!compat.compatible) {
        setValidationError(
          compat.reason +
            " Open the local browse app directly, or expose your server via an HTTPS tunnel."
        );
        setIsCompatibilityError(true);
        return;
      }
    }

    setValidating(true);
    setValidationError(null);
    setIsCompatibilityError(false);

    const error = await probeRemoteHost(url, newToken.trim());
    if (error) {
      setValidationError(error);
      setValidating(false);
      return;
    }

    const profile: HostProfile = {
      id: `host-${Date.now()}`,
      label: newLabel.trim() || url,
      base_url: url,
      token: newToken.trim(),
      cli_kind: newCliKind,
      is_default: false,
    };
    saveHostProfile(profile);
    setSelectedHostId(profile.id);
    setAddingNew(false);
    setNewUrl("");
    setNewLabel("");
    setNewToken("");
    setNewCliKind("copilot");
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
    };
    saveHostProfile(profile);
    setSelectedHostId(profile.id);
    setAddingNew(false);
    setNewUrl("");
    setNewLabel("");
    setNewToken("");
    setNewCliKind("copilot");
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
              <p className="text-muted-foreground text-xs">Same-origin default</p>
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
      </div>

      {/* Actions bar */}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setAddingNew((v) => !v)}
          aria-label="Add remote host"
          aria-expanded={addingNew}
        >
          <Plus className="mr-1.5 size-3.5" />
          Add host
        </Button>
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

      {/* Add host form */}
      {addingNew && (
        <div className="space-y-3 rounded-lg border p-4" data-testid="host-add-form">
          <p className="text-muted-foreground text-xs">
            Add a public tunnel URL (e.g. ngrok, Cloudflare Tunnel, VS Code forwarded port) that
            exposes the Copilot CLI operator API.
          </p>
          <input
            type="url"
            value={newUrl}
            onChange={(e) => {
              setNewUrl(e.target.value);
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
                setValidationError(null);
                setIsCompatibilityError(false);
              }}
            >
              Cancel
            </Button>
            {validationError && !isCompatibilityError ? (
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
          </div>
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
