/**
 * Shared surface styling for browse-ui overlays and embedded panels.
 *
 * All floating surfaces (select dropdowns, dropdown menus, dialogs, popovers,
 * suggestion lists) MUST use POPUP_SURFACE_BASE so the surface stays visually
 * solid and readable over busy page content. Embedded panels that are not
 * portalled can use PANEL_SURFACE_BASE / SurfacePanel.
 */

/** Opaque background + foreground text shared by every popup surface. */
export const POPUP_SURFACE_BG = "bg-popover text-popover-foreground" as const;

/** Embedded card/panel surface for non-portalled forms that still need a stable background. */
export const PANEL_SURFACE_BASE =
  "bg-card text-card-foreground border border-border/80 shadow-sm" as const;

/**
 * Full floating panel: opaque background, matching foreground, ring border,
 * and stronger elevation. Always use this for elements that float *above* page
 * content (dropdown menus, select popups, dialogs, popovers, suggestion lists).
 *
 * Do NOT add opacity fractions, backdrop-blur, or bg-transparent overrides —
 * popup surfaces must remain fully readable against any page background.
 */
export const POPUP_SURFACE_BASE =
  `${POPUP_SURFACE_BG} border border-border/80 ring-border ring-1 shadow-lg overflow-hidden` as const;
