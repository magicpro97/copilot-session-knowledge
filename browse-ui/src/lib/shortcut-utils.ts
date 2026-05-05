/**
 * Platform-aware keyboard shortcut display utilities.
 *
 * All functions are SSR-safe: `detectShortcutPlatform` returns "unknown"
 * on the server when `navigator` is unavailable.
 */

export type ShortcutPlatform = "mac" | "win" | "unknown";

/**
 * Detect the current platform from navigator.userAgent.
 * Returns "unknown" during server-side rendering.
 */
export function detectShortcutPlatform(): ShortcutPlatform {
  if (typeof navigator === "undefined") return "unknown";
  const ua = navigator.userAgent;
  if (/Mac|iPod|iPhone|iPad/.test(ua)) return "mac";
  if (/Win|Linux|Android|CrOS/.test(ua)) return "win";
  return "unknown";
}

/**
 * Return the modifier key label for a platform.
 * - mac     → "⌘"
 * - win     → "Ctrl"
 * - unknown → "⌘/Ctrl"
 */
export function getModLabel(platform: ShortcutPlatform): string {
  if (platform === "mac") return "⌘";
  if (platform === "win") return "Ctrl";
  return "⌘/Ctrl";
}

/**
 * Format a modifier+key shortcut for the given platform.
 *
 * @param key     - Key label for Windows/Linux/unknown (e.g. "K", "Enter")
 * @param platform - Target platform
 * @param macKey  - Optional alternative key symbol for Mac (e.g. "↩" for Enter)
 *
 * Examples:
 *   formatModShortcut("K", "mac")           → "⌘K"
 *   formatModShortcut("K", "win")           → "Ctrl+K"
 *   formatModShortcut("K", "unknown")       → "⌘/Ctrl+K"
 *   formatModShortcut("Enter", "mac", "↩")  → "⌘↩"
 *   formatModShortcut("Enter", "win", "↩")  → "Ctrl+Enter"
 *   formatModShortcut("Enter", "unknown", "↩") → "⌘/Ctrl+Enter"
 */
export function formatModShortcut(
  key: string,
  platform: ShortcutPlatform,
  macKey?: string
): string {
  if (platform === "mac") return `⌘${macKey ?? key}`;
  if (platform === "win") return `Ctrl+${key}`;
  return `⌘/Ctrl+${key}`;
}

/**
 * Resolve a cross-platform shortcut string in "⌘/Ctrl+KEY" format to the
 * platform-specific form.  Strings that don't match the pattern are
 * returned unchanged.
 *
 * Examples:
 *   resolveModShortcut("⌘/Ctrl+K", "mac")  → "⌘K"
 *   resolveModShortcut("⌘/Ctrl+K", "win")  → "Ctrl+K"
 *   resolveModShortcut("G then S", "mac")   → "G then S"
 */
export function resolveModShortcut(raw: string, platform: ShortcutPlatform): string {
  const match = raw.match(/^⌘\/Ctrl\+(.+)$/);
  if (!match) return raw;
  const key = match[1] ?? "";
  return formatModShortcut(key, platform);
}
