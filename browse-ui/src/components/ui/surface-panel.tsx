"use client";

import * as React from "react";

import { PANEL_SURFACE_BASE } from "@/components/ui/popup-surface";
import { cn } from "@/lib/utils";

export function SurfacePanel({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="surface-panel" className={cn(PANEL_SURFACE_BASE, className)} {...props} />;
}
