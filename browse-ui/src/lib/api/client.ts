import { getToken, clearToken } from "@/lib/auth";
import type { HostProfile } from "@/lib/api/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

/**
 * Build a URL by appending `path` to `base`, preserving any path prefix
 * already present in `base`.
 *
 * Unlike `new URL(absolutePath, base)`, this function does NOT discard the
 * base's path component when `path` starts with `/`.  For example:
 *   buildHostUrl("https://proxy.example.com/copilot", "/api/sessions")
 *   → "https://proxy.example.com/copilot/api/sessions"   (prefix preserved)
 */
export function buildHostUrl(base: string, path: string): URL {
  const baseUrl = new URL(base);
  const basePath = baseUrl.pathname.replace(/\/$/, "");
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  baseUrl.pathname = basePath + cleanPath;
  return baseUrl;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const base = API_BASE || (typeof window !== "undefined" ? window.location.origin : "");
  const url = buildHostUrl(base, path);

  const headers = new Headers(init?.headers);
  // Send token via Authorization header to keep it out of browser-visible URLs
  // (fixes #34: same-origin requests must not expose token in the URL).
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(url.toString(), { ...init, headers });

  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined") {
      window.location.href = "/v2/sessions";
    }
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    throw new Error(`API ${res.status}: ${await res.text()}`);
  }

  return res.json() as Promise<T>;
}

/**
 * Fetch helper that routes to a specific host profile.
 *
 * - Uses `host.base_url` as the base URL when set; falls back to same-origin.
 * - Preserves path prefixes in `base_url` (e.g. `https://proxy.example.com/copilot`
 *   routes to `/copilot/api/...` rather than stripping the prefix) — fixes #31.
 * - For all hosts, sends the token in the `Authorization` header to keep
 *   credentials out of browser-visible URLs and proxy logs — fixes #34.
 */
export async function hostFetch<T>(
  path: string,
  host: HostProfile,
  init?: RequestInit
): Promise<T> {
  const isRemote = host.base_url.length > 0;
  const base = isRemote
    ? host.base_url
    : API_BASE || (typeof window !== "undefined" ? window.location.origin : "");

  const token = isRemote ? host.token : host.token || getToken();

  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  // buildHostUrl preserves any path prefix in base_url (issue #31).
  const url = buildHostUrl(base, normalizedPath);

  const headers = new Headers(init?.headers);
  // Use Authorization header for both local and remote hosts; token never
  // appears in the URL (fixes #34 same-origin case, consistent with remote).
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const res = await fetch(url.toString(), { ...init, headers });

  if (res.status === 401) {
    if (!isRemote) {
      clearToken();
      if (typeof window !== "undefined") {
        window.location.href = "/v2/sessions";
      }
    }
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    throw new Error(`API ${res.status}: ${await res.text()}`);
  }

  return res.json() as Promise<T>;
}
