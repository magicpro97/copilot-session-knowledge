/**
 * Shared opaque popup/list surface styling.
 *
 * All floating surfaces (select dropdowns, dropdown menus, popovers, command
 * palettes, suggestion lists) MUST use POPUP_SURFACE_BASE (or POPUP_SURFACE_BG)
 * so the surface stays fully opaque and the transparent-popup bug cannot
 * silently re-emerge.
 *
 * Usage in a component:
 *   import { cn } from "@/lib/utils";
 *   import { POPUP_SURFACE_BASE } from "@/components/ui/popup-surface";
 *
 *   // For floating panels with ring + shadow:
 *   className={cn(POPUP_SURFACE_BASE, "my-layout-classes", className)}
 *
 *   // For container-embedded surfaces (e.g. Command inside Dialog):
 *   className={cn(POPUP_SURFACE_BG, "my-layout-classes", className)}
 */

/** Opaque background + foreground text shared by every popup surface. */
export const POPUP_SURFACE_BG = "bg-popover text-popover-foreground" as const;

/**
 * Full floating panel: opaque background, matching foreground, ring border,
 * and drop-shadow.  Always use this for elements that float *above* page
 * content (dropdown menus, select popups, popovers, suggestion lists).
 *
 * Do NOT add opacity fractions, backdrop-blur, or bg-transparent overrides —
 * popup surfaces must remain fully readable against any page background.
 */
export const POPUP_SURFACE_BASE = `${POPUP_SURFACE_BG} ring-border shadow-md ring-1` as const;
