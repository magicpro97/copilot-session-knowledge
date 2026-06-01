"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useUpdateKnowledgeEntry } from "@/lib/api/hooks";
import type { KnowledgeInsightsEntry } from "@/lib/api/types";
import type { HostProfile } from "@/lib/api/types";
import { LOCAL_HOST } from "@/lib/host-profiles";
import { cn } from "@/lib/utils";

export interface EntryEditorProps {
  entry: KnowledgeInsightsEntry & { tags?: string; description?: string };
  host?: HostProfile;
  /** Called after a successful save so the parent can update its local state. */
  onSaved?: (updated: {
    id: number;
    title: string;
    description: string;
    tags: string;
    confidence: number;
  }) => void;
  className?: string;
}

interface EditState {
  title: string;
  description: string;
  tags: string;
  confidence: number;
}

/**
 * Inline editor for a knowledge entry card.
 *
 * Shows a pencil icon button on hover. When in edit mode it replaces the card
 * content with an inline form (title, description, tags, confidence slider).
 * Save issues PUT /api/knowledge/{id} with optimistic local update.
 */
export function EntryEditor({ entry, host = LOCAL_HOST, onSaved, className }: EntryEditorProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editState, setEditState] = useState<EditState>({
    title: entry.title,
    description: entry.summary ?? entry.description ?? "",
    tags: entry.tags ?? "",
    confidence: entry.confidence,
  });
  // Optimistic display values (updated immediately on save, rolled back on error)
  const [displayState, setDisplayState] = useState<EditState>({
    title: entry.title,
    description: entry.summary ?? entry.description ?? "",
    tags: entry.tags ?? "",
    confidence: entry.confidence,
  });

  const updateMutation = useUpdateKnowledgeEntry(host);

  function handleEditClick() {
    setEditState({ ...displayState });
    setIsEditing(true);
  }

  function handleCancel() {
    setIsEditing(false);
    setEditState({ ...displayState });
  }

  async function handleSave() {
    // Optimistic update
    const next = { ...editState };
    const prev = { ...displayState };
    setDisplayState(next);
    setIsEditing(false);

    try {
      await updateMutation.mutateAsync({
        id: entry.id,
        title: next.title,
        description: next.description,
        tags: next.tags,
        confidence: next.confidence,
      });
      toast.success("Entry updated");
      onSaved?.({ id: entry.id, ...next });
    } catch (err) {
      // Roll back
      setDisplayState(prev);
      toast.error(err instanceof Error ? err.message : "Failed to update entry");
    }
  }

  if (isEditing) {
    return (
      <div
        className={cn("space-y-2 rounded border px-2 py-1.5", className)}
        data-testid="entry-editor-form"
      >
        <div>
          <label className="text-muted-foreground text-[10px] tracking-wide uppercase">Title</label>
          <Input
            value={editState.title}
            onChange={(e) => setEditState((s) => ({ ...s, title: e.target.value }))}
            className="mt-0.5 h-7 text-xs"
            data-testid="entry-editor-title"
          />
        </div>
        <div>
          <label className="text-muted-foreground text-[10px] tracking-wide uppercase">
            Description
          </label>
          <textarea
            value={editState.description}
            onChange={(e) => setEditState((s) => ({ ...s, description: e.target.value }))}
            rows={4}
            className="border-input focus-visible:border-ring focus-visible:ring-ring/50 mt-0.5 w-full resize-none rounded-lg border bg-transparent px-2.5 py-1 text-xs outline-none focus-visible:ring-2"
            data-testid="entry-editor-description"
          />
        </div>
        <div>
          <label className="text-muted-foreground text-[10px] tracking-wide uppercase">
            Tags (comma-separated)
          </label>
          <Input
            value={editState.tags}
            onChange={(e) => setEditState((s) => ({ ...s, tags: e.target.value }))}
            className="mt-0.5 h-7 text-xs"
            placeholder="tag1, tag2"
            data-testid="entry-editor-tags"
          />
        </div>
        <div>
          <label className="text-muted-foreground text-[10px] tracking-wide uppercase">
            Confidence:{" "}
            <span className="font-medium tabular-nums">{editState.confidence.toFixed(2)}</span>
          </label>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={editState.confidence}
            onChange={(e) =>
              setEditState((s) => ({ ...s, confidence: parseFloat(e.target.value) }))
            }
            className="accent-primary mt-0.5 h-4 w-full"
            data-testid="entry-editor-confidence"
          />
        </div>
        <div className="flex justify-end gap-1 pt-0.5">
          <Button
            variant="ghost"
            size="xs"
            onClick={handleCancel}
            data-testid="entry-editor-cancel"
          >
            Cancel
          </Button>
          <Button
            variant="default"
            size="xs"
            onClick={handleSave}
            disabled={!editState.title.trim() || updateMutation.isPending}
            data-testid="entry-editor-save"
          >
            {updateMutation.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn("group relative rounded border px-2 py-1.5", className)}
      data-testid="entry-editor-display"
    >
      <div className="flex items-start gap-2">
        <span className="text-foreground min-w-0 flex-1 truncate text-xs font-medium">
          {displayState.title}
        </span>
        <Badge variant="outline" className="shrink-0 text-[10px]">
          conf {displayState.confidence.toFixed(2)}
        </Badge>
        <button
          onClick={handleEditClick}
          aria-label="Edit entry"
          className="text-muted-foreground hover:text-foreground shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
          data-testid="entry-editor-edit-btn"
        >
          ✏️
        </button>
      </div>
      {displayState.description ? (
        <p className="text-muted-foreground mt-0.5 line-clamp-1 text-[11px]">
          {displayState.description}
        </p>
      ) : null}
    </div>
  );
}
