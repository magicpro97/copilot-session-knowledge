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
 *
 * Query strings and hashes passed as part of `path` remain in `search` / `hash`
 * rather than being percent-encoded into the pathname.
 */
export function buildHostUrl(base: string, path: string): URL {
  const baseUrl = new URL(base);
  const basePath = baseUrl.pathname.replace(/\/$/, "");
  const [pathAndQuery, hashPart = ""] = path.split("#", 2);
  const [pathnamePart, queryPart = ""] = pathAndQuery.split("?", 2);
  const cleanPath = pathnamePart.startsWith("/") ? pathnamePart : `/${pathnamePart}`;
  baseUrl.pathname = basePath + cleanPath;
  baseUrl.search = queryPart ? `?${queryPart}` : "";
  baseUrl.hash = hashPart ? `#${hashPart}` : "";
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
      window.location.href = "/sessions";
    }
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    throw new Error(`API ${res.status}: ${await res.text()}`);
  }

  return res.json() as Promise<T>;
}

export async function hostRequest(
  path: string,
  host: HostProfile,
  init?: RequestInit
): Promise<Response> {
  const isRemote = host.base_url.length > 0;
  const base = isRemote
    ? host.base_url
    : API_BASE || (typeof window !== "undefined" ? window.location.origin : "");

  const token = isRemote ? host.token : host.token || getToken();
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const url = buildHostUrl(base, normalizedPath);

  const headers = new Headers(init?.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const res = await fetch(url.toString(), { ...init, headers });

  if (res.status === 401) {
    if (!isRemote) {
      clearToken();
      if (typeof window !== "undefined") {
        window.location.href = "/sessions";
      }
    }
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    throw new Error(`API ${res.status}: ${await res.text()}`);
  }

  return res;
}

/**
 * Fetch debug log entries for a specific operator session run.
 *
 * Wraps `hostFetch` with a typed return; the caller is responsible for
 * constructing the full path with query parameters.
 *
 * Path: /api/operator/sessions/{sessionId}/runs/{runId}/debug
 * Params: from, limit (max 100), kind, level, since
 *
 * Returns a raw Response so the caller (hook) can stream or parse JSON.
 * Use `hostFetch` when you need the parsed body directly.
 */
export async function fetchDebugLog(
  path: string,
  host: HostProfile,
  init?: RequestInit
): Promise<Response> {
  return hostRequest(path, host, init);
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
  const res = await hostRequest(path, host, init);
  return res.json() as Promise<T>;
}
