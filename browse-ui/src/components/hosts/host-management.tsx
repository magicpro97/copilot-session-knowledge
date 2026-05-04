"use client";

import { useEffect, useState, type ComponentProps } from "react";
import { CheckCircle2, Globe, Plus, RotateCcw, ServerCog, Star, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { HostProfile } from "@/lib/api/types";
import {
  BROWSE_HOST_CHANGE_EVENT,
  LOCAL_HOST,
  LOCAL_HOST_ID,
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
export function HostManagement({ className, ...props }: ComponentProps<"div">) {
  const [allHosts, setAllHosts] = useState<HostProfile[]>([LOCAL_HOST]);
  const [selectedId, setSelectedIdLocal] = useState<string | null>(null);
  const [addingNew, setAddingNew] = useState(false);
  const [newUrl, setNewUrl] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newToken, setNewToken] = useState("");
  const [newCliKind, setNewCliKind] = useState("copilot");

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

  function handleAdd() {
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
    refresh();
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
            onChange={(e) => setNewUrl(e.target.value)}
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
              }}
            >
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              className="h-7 text-xs"
              onClick={handleAdd}
              disabled={!newUrl.trim()}
              data-testid="save-host-btn"
            >
              Save host
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
