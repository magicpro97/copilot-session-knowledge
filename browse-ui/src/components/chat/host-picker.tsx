"use client";

import { useEffect, useState } from "react";
import { Globe, Plus, ServerCog, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { HostProfile } from "@/lib/api/types";
import {
  BROWSE_HOST_CHANGE_EVENT,
  getAllHostProfiles,
  saveHostProfile,
  deleteHostProfile,
  setSelectedHostId,
  LOCAL_HOST,
  LOCAL_HOST_ID,
} from "@/lib/host-profiles";

type HostPickerProps = {
  /** Currently selected host profile. */
  value: HostProfile;
  onChange: (host: HostProfile) => void;
  disabled?: boolean;
  className?: string;
};

const CLI_KIND_OPTIONS = [
  { value: "copilot", label: "GitHub Copilot CLI" },
  { value: "claude", label: "Claude" },
  { value: "other", label: "Other" },
];

/**
 * Lets the user pick between the local origin and any saved agent host profiles
 * (public tunnel URLs). Saved profiles are persisted via @/lib/host-profiles.
 *
 * Host-scoped operator API hooks are available via the client tentacle's
 * hostFetch contract. The selected profile is passed directly to those hooks.
 */
export function HostPicker({ value, onChange, disabled, className }: HostPickerProps) {
  const [allHosts, setAllHosts] = useState<HostProfile[]>([LOCAL_HOST]);
  const [addingNew, setAddingNew] = useState(false);
  const [newUrl, setNewUrl] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newToken, setNewToken] = useState("");
  const [newCliKind, setNewCliKind] = useState("copilot");

  useEffect(() => {
    const refresh = () => setAllHosts(getAllHostProfiles());
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
    const label = newLabel.trim() || url;
    const profile: HostProfile = {
      id: `host-${Date.now()}`,
      label,
      base_url: url,
      token: newToken.trim(),
      cli_kind: newCliKind,
      is_default: false,
    };
    saveHostProfile(profile);
    const updated = getAllHostProfiles();
    setAllHosts(updated);
    onChange(profile);
    setSelectedHostId(profile.id);
    setAddingNew(false);
    setNewUrl("");
    setNewLabel("");
    setNewToken("");
    setNewCliKind("copilot");
  }

  function handleRemove(id: string) {
    deleteHostProfile(id);
    const updated = getAllHostProfiles();
    setAllHosts(updated);
    if (value.id === id) {
      onChange(LOCAL_HOST);
      setSelectedHostId(LOCAL_HOST_ID);
    }
  }

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex items-center gap-2">
        <Select
          value={value.id}
          onValueChange={(id) => {
            const host = allHosts.find((h) => h.id === id) ?? LOCAL_HOST;
            onChange(host);
            if (id && id !== LOCAL_HOST_ID) {
              setSelectedHostId(id);
            } else {
              setSelectedHostId(LOCAL_HOST_ID);
            }
          }}
          disabled={disabled}
        >
          <SelectTrigger className="flex-1" aria-label="Agent host">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {allHosts.map((host) => (
              <SelectItem key={host.id} value={host.id}>
                <div className="flex items-center gap-2">
                  {host.id === LOCAL_HOST_ID ? (
                    <ServerCog className="text-muted-foreground size-3.5 shrink-0" />
                  ) : (
                    <Globe className="text-muted-foreground size-3.5 shrink-0" />
                  )}
                  <span>{host.label}</span>
                  {host.base_url ? (
                    <span className="text-muted-foreground max-w-[160px] truncate font-mono text-xs">
                      {host.base_url.replace(/^https?:\/\//, "")}
                    </span>
                  ) : null}
                </div>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-9 shrink-0"
          onClick={() => setAddingNew((v) => !v)}
          disabled={disabled}
          aria-label="Add agent host"
          title="Save a public tunnel URL as an agent host"
        >
          <Plus className="size-4" />
        </Button>
      </div>

      {addingNew && (
        <div className="space-y-2 rounded-lg border p-3" data-testid="host-add-form">
          <p className="text-muted-foreground text-xs">
            Add a public tunnel URL (e.g. ngrok, Cloudflare Tunnel, VS Code forwarded port).
          </p>
          <input
            type="url"
            value={newUrl}
            onChange={(e) => setNewUrl(e.target.value)}
            placeholder="https://abc123.ngrok.io"
            aria-label="Tunnel URL"
            className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-1.5 font-mono text-xs outline-none focus-visible:ring-2"
          />
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
          <Select
            value={newCliKind}
            onValueChange={(v) => {
              if (v !== null) setNewCliKind(v);
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
            >
              Save host
            </Button>
          </div>
        </div>
      )}

      {value.id !== LOCAL_HOST_ID && value.base_url ? (
        <div
          className="bg-muted flex items-center justify-between rounded-md px-2 py-1 text-xs"
          data-testid="host-active-url"
        >
          <span className="text-muted-foreground truncate font-mono">
            {value.base_url.replace(/^https?:\/\//, "")}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="ml-2 size-5 shrink-0"
            onClick={() => handleRemove(value.id)}
            disabled={disabled}
            aria-label={`Remove host ${value.label}`}
          >
            <Trash2 className="size-3" />
          </Button>
        </div>
      ) : null}
    </div>
  );
}
