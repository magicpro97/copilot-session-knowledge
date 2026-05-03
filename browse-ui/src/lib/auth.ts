const TOKEN_STORAGE_KEY = "browse_token";

function hostTokenKey(hostId: string): string {
  return `browse_token_host_${hostId}`;
}

export function getToken(): string {
  if (typeof window !== "undefined") {
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get("token");
    if (urlToken) {
      sessionStorage.setItem(TOKEN_STORAGE_KEY, urlToken);
      const clean = new URL(window.location.href);
      clean.searchParams.delete("token");
      window.history.replaceState({}, "", clean.toString());
      return urlToken;
    }
    return sessionStorage.getItem(TOKEN_STORAGE_KEY) ?? "";
  }
  return "";
}

export function clearToken(): void {
  if (typeof window !== "undefined") {
    sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  }
}

/** Returns the stored token for a specific host profile id. */
export function getTokenForHost(hostId: string): string {
  if (typeof window !== "undefined") {
    return sessionStorage.getItem(hostTokenKey(hostId)) ?? "";
  }
  return "";
}

/** Persists or clears a token for a specific host profile id. */
export function setTokenForHost(hostId: string, token: string): void {
  if (typeof window !== "undefined") {
    if (token) {
      sessionStorage.setItem(hostTokenKey(hostId), token);
    } else {
      sessionStorage.removeItem(hostTokenKey(hostId));
    }
  }
}

/** Removes the stored token for a specific host profile id. */
export function clearTokenForHost(hostId: string): void {
  if (typeof window !== "undefined") {
    sessionStorage.removeItem(hostTokenKey(hostId));
  }
}
