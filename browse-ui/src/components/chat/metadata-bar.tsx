"use client";

import { FolderOpen, Cpu, Settings2, Hash } from "lucide-react";
import { cn } from "@/lib/utils";
import type { OperatorSession } from "@/lib/api/types";

type MetadataBarProps = {
  session: OperatorSession;
  className?: string;
};

/**
 * Compact metadata bar showing workspace, model, mode, and run count for the
 * active operator session.
 */
export function MetadataBar({ session, className }: MetadataBarProps) {
  return (
    <div
      className={cn(
        "bg-muted/30 flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-4 py-1.5 text-xs",
        className
      )}
    >
      <span className="text-muted-foreground flex items-center gap-1">
        <FolderOpen className="size-3" />
        <span className="font-mono">{session.workspace}</span>
      </span>
      <span className="text-muted-foreground flex items-center gap-1">
        <Cpu className="size-3" />
        <span>{session.model}</span>
      </span>
      <span className="text-muted-foreground flex items-center gap-1">
        <Settings2 className="size-3" />
        <span>{session.mode}</span>
      </span>
      <span className="text-muted-foreground flex items-center gap-1">
        <Hash className="size-3" />
        <span>
          {session.run_count} run{session.run_count !== 1 ? "s" : ""}
        </span>
      </span>
    </div>
  );
}
