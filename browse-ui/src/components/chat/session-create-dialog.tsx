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
import { WorkspacePicker } from "./workspace-picker";
import type { CreateOperatorSessionRequest } from "@/lib/api/types";

const COPILOT_MODELS = [
  { value: "claude-sonnet-4.5", label: "Claude Sonnet 4.5" },
  { value: "claude-opus-4.5", label: "Claude Opus 4.5" },
  { value: "gpt-4o", label: "GPT-4o" },
  { value: "o3", label: "o3" },
];

export const COPILOT_MODES = [
  { value: "interactive", label: "Interactive" },
  { value: "plan", label: "Plan" },
  { value: "autopilot", label: "Autopilot" },
];

type SessionCreateDialogProps = {
  onSubmit: (payload: CreateOperatorSessionRequest) => void;
  loading?: boolean;
};

export function SessionCreateDialog({ onSubmit, loading }: SessionCreateDialogProps) {
  const nameId = useId();
  const workspaceId = useId();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [model, setModel] = useState(COPILOT_MODELS[0].value);
  const [mode, setMode] = useState(COPILOT_MODES[0].value);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!workspace.trim()) return;
    onSubmit({
      name: name.trim() || `Chat ${new Date().toLocaleString()}`,
      workspace: workspace.trim(),
      model,
      mode,
    });
    setOpen(false);
    setName("");
    setWorkspace("");
    setModel(COPILOT_MODELS[0].value);
    setMode(COPILOT_MODES[0].value);
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
            configure Copilot CLI settings.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor={workspaceId} className="text-sm font-medium">
              Workspace <span className="text-destructive">*</span>
            </label>
            <WorkspacePicker
              id={workspaceId}
              value={workspace}
              onChange={setWorkspace}
              disabled={loading}
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
              <label className="text-sm font-medium">Model</label>
              <Select
                value={model}
                onValueChange={(value) => {
                  if (value) setModel(value);
                }}
                disabled={loading}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {COPILOT_MODELS.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
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
