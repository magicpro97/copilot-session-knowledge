const NUMBER_FORMATTER = new Intl.NumberFormat();

export function formatSessionIdBadgeText(sessionId: string | null | undefined, length = 8): string {
  if (!sessionId) return "—";
  return sessionId.slice(0, length);
}

export function formatNumber(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return NUMBER_FORMATTER.format(value);
}

export function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null || Number.isNaN(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;

  const units = ["KB", "MB", "GB", "TB"];
  let size = bytes / 1024;
  let unitIndex = 0;

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }

  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}

function parseDateInput(input: string | number | Date | null | undefined): Date | null {
  if (input == null) return null;
  const date = input instanceof Date ? input : new Date(input);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatRelativeTime(
  input: string | number | Date | null | undefined,
  now: number = Date.now()
): string {
  const targetDate = parseDateInput(input);
  if (!targetDate) return "—";

  const diffMs = targetDate.getTime() - now;
  const absSeconds = Math.abs(Math.round(diffMs / 1000));
  const isFuture = diffMs > 0;
  const prefix = isFuture ? "in " : "";
  const suffix = isFuture ? "" : " ago";

  if (absSeconds < 45) return isFuture ? "soon" : "just now";

  if (absSeconds < 3600) {
    return `${prefix}${Math.round(absSeconds / 60)}m${suffix}`;
  }
  if (absSeconds < 86_400) {
    return `${prefix}${Math.round(absSeconds / 3600)}h${suffix}`;
  }
  if (absSeconds < 604_800) {
    return `${prefix}${Math.round(absSeconds / 86_400)}d${suffix}`;
  }
  if (absSeconds < 2_592_000) {
    return `${prefix}${Math.round(absSeconds / 604_800)}w${suffix}`;
  }

  return targetDate.toLocaleDateString();
}
