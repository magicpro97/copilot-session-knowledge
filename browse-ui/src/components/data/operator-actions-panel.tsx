"use client";

import { Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { OperatorAction } from "@/lib/api/types";

async function copyCommand(command: string) {
  if (!command || typeof navigator === "undefined" || !navigator.clipboard) return;
  try {
    await navigator.clipboard.writeText(command);
  } catch {
    // Ignore clipboard failures in unsupported environments.
  }
}

type OperatorActionsPanelProps = {
  actions: OperatorAction[];
  /** Label shown above the action list. Defaults to "Operator checks (read-only)". */
  label?: string;
  /** Secondary note shown below the label. Defaults to a generic copy-only disclaimer. */
  note?: string;
};

/**
 * Renders a list of read-only operator actions with copy-to-clipboard buttons.
 *
 * Actions are display-only — the browser never executes them. Each action
 * provides a `command` string the operator can copy and run manually.
 */
export function OperatorActionsPanel({
  actions,
  label = "Operator checks (read-only)",
  note = "Safe command-line checks for local visibility only. No write operations are listed.",
}: OperatorActionsPanelProps) {
  if (actions.length === 0) return null;

  return (
    <div className="bg-card space-y-2 rounded-lg border p-3">
      <p className="text-foreground text-xs font-medium">{label}</p>
      <p className="text-muted-foreground text-xs">{note}</p>
      <div className="space-y-2">
        {actions.map((action) => (
          <div key={action.id} className="bg-background rounded-md border p-2 text-xs">
            <p className="text-foreground font-medium">{action.title}</p>
            <p className="text-muted-foreground">{action.description}</p>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
                {action.command}
              </code>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-6 px-2 text-[11px]"
                onClick={() => void copyCommand(action.command)}
              >
                <Copy className="size-3" />
                Copy
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
