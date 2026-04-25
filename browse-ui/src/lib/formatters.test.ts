import { describe, expect, it } from "vitest";

import {
  formatFileSize,
  formatNumber,
  formatRelativeTime,
  formatSessionIdBadgeText,
} from "@/lib/formatters";

describe("formatters", () => {
  it("formats session id badges", () => {
    expect(formatSessionIdBadgeText("1234567890abcdef")).toBe("12345678");
    expect(formatSessionIdBadgeText(null)).toBe("—");
  });

  it("formats numbers", () => {
    expect(formatNumber(1234567)).toBe("1,234,567");
    expect(formatNumber(undefined)).toBe("—");
  });

  it("formats file sizes", () => {
    expect(formatFileSize(500)).toBe("500 B");
    expect(formatFileSize(2048)).toBe("2.0 KB");
    expect(formatFileSize(1024 * 1024)).toBe("1.0 MB");
    expect(formatFileSize(-1)).toBe("—");
  });

  it("formats relative times for past and future values", () => {
    const now = new Date("2025-01-01T12:00:00Z").getTime();
    expect(formatRelativeTime("2025-01-01T11:00:00Z", now)).toBe("1h ago");
    expect(formatRelativeTime("2025-01-01T13:00:00Z", now)).toBe("in 1h");
    expect(formatRelativeTime("invalid-date", now)).toBe("—");
  });
});
