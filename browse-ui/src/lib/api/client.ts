import { getToken, clearToken } from "@/lib/auth";
import type { HostProfile } from "@/lib/api/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const base = API_BASE || (typeof window !== "undefined" ? window.location.origin : "");
  const url = new URL(path, base);

  if (token) url.searchParams.set("token", token);

  const res = await fetch(url.toString(), { ...init });

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
 * - For remote hosts, sends the token in the `Authorization` header to avoid
 *   leaking credentials into URL-logged proxies/servers.
 * - For local (same-origin) hosts, sends token as a URL query param to stay
 *   backward-compatible with the existing backend token middleware.
 *
 * NOTE: SSE streams via `EventSource` do not support custom headers. For remote
 * hosts use `createOperatorStreamUrl()` (in hooks.ts) which appends token as a
 * query param instead.
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
  const url = new URL(normalizedPath, base);

  const headers = new Headers(init?.headers);
  if (token) {
    if (isRemote) {
      headers.set("Authorization", `Bearer ${token}`);
    } else {
      url.searchParams.set("token", token);
    }
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
