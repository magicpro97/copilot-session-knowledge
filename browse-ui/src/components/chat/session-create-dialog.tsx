"use client";

import { useId, useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useOperatorModelCatalog } from "@/lib/api/hooks";
import { LOCAL_HOST } from "@/lib/host-profiles";
import type { HostProfile, CreateOperatorSessionRequest } from "@/lib/api/types";
import { WorkspacePicker } from "./workspace-picker";
import { HostPicker } from "./host-picker";

export const COPILOT_MODES = [
  { value: "interactive", label: "Interactive" },
  { value: "plan", label: "Plan" },
  { value: "autopilot", label: "Autopilot" },
];

/**
 * Extends the base create-session request with the selected `HostProfile` so the
 * shell can pass the right host to all downstream API hooks.
 * The `host` field is stripped before the actual POST to the operator API.
 */
export type CreateSessionPayload = CreateOperatorSessionRequest & {
  host: HostProfile;
};

type SessionCreateDialogProps = {
  onSubmit: (payload: CreateSessionPayload) => void;
  loading?: boolean;
};

export function SessionCreateDialog({ onSubmit, loading }: SessionCreateDialogProps) {
  const nameId = useId();
  const workspaceId = useId();
  const modelId = useId();
  const modelListId = useId();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [model, setModel] = useState("");
  const [mode, setMode] = useState(COPILOT_MODES[0].value);
  const [host, setHost] = useState<HostProfile>(LOCAL_HOST);
  const modelCatalogQuery = useOperatorModelCatalog(host, open);
  const modelSuggestions = modelCatalogQuery.data?.models ?? [];
  const defaultModel = modelCatalogQuery.data?.default_model ?? "";

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!workspace.trim()) return;
    onSubmit({
      name: name.trim() || `Chat ${new Date().toLocaleString()}`,
      workspace: workspace.trim(),
      model: model.trim() || undefined,
      mode,
      host,
    });
    setOpen(false);
    setName("");
    setWorkspace("");
    setModel("");
    setMode(COPILOT_MODES[0].value);
    setHost(LOCAL_HOST);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button size="sm" className="w-full" aria-label="New chat session">
            <Plus className="mr-2 size-4" />
            New Chat
          </Button>
        }
      />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Start a new chat session</DialogTitle>
          <DialogDescription>
            Choose a workspace under <code className="bg-muted rounded px-1 text-xs">~/</code> and
            configure agent settings.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Agent Host</label>
            <HostPicker value={host} onChange={setHost} disabled={loading} />
            <p className="text-muted-foreground text-xs">
              Local (same origin) or a saved public tunnel URL.
            </p>
          </div>
          <div className="space-y-1.5">
            <label htmlFor={workspaceId} className="text-sm font-medium">
              Workspace <span className="text-destructive">*</span>
            </label>
            <WorkspacePicker
              id={workspaceId}
              value={workspace}
              onChange={setWorkspace}
              disabled={loading}
              host={host}
            />
            <p className="text-muted-foreground text-xs">
              Must be a path under <code>~/</code> on the host.
            </p>
          </div>
          <div className="space-y-1.5">
            <label htmlFor={nameId} className="text-sm font-medium">
              Name
            </label>
            <input
              id={nameId}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My session"
              disabled={loading}
              className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-2 text-sm transition-colors outline-none focus-visible:ring-2 disabled:opacity-50"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label htmlFor={modelId} className="text-sm font-medium">
                Model
              </label>
              <input
                id={modelId}
                type="text"
                list={modelListId}
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={defaultModel || "Leave blank to use the CLI default"}
                disabled={loading}
                className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-2 text-sm transition-colors outline-none focus-visible:ring-2 disabled:opacity-50"
              />
              <datalist id={modelListId}>
                {modelSuggestions.map((m) => (
                  <option key={m.id} value={m.id} label={m.display_name} />
                ))}
              </datalist>
              <p className="text-muted-foreground text-xs">Leave blank to use the CLI default.</p>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Mode</label>
              <Select
                value={mode}
                onValueChange={(value) => {
                  if (value) setMode(value);
                }}
                disabled={loading}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {COPILOT_MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <DialogClose
              render={
                <Button type="button" variant="outline" disabled={loading}>
                  Cancel
                </Button>
              }
            />
            <Button type="submit" disabled={loading || !workspace.trim()}>
              {loading ? "Creating…" : "Start Chat"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
