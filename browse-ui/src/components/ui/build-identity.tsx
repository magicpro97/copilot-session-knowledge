"use client";

import { useEffect, useState } from "react";

interface VersionInfo {
  version?: string;
  buildHash: string;
  builtAt: string;
  nodeVersion?: string;
  basePath?: string;
}

/**
 * Fetches version.json (no-store) and renders:
 *   Build {buildHash} · {builtAt formatted}
 *
 * Safe: never exposes env vars, branch, actor, or secrets.
 * Issue #520 — deploy SHA visibility.
 */
export function getBuildIdentityUrl(): string {
  const explicitBase = document.querySelector("base")?.href;
  if (explicitBase) {
    return new URL("version.json", explicitBase).href;
  }

  const nextAsset = document.querySelector<HTMLScriptElement | HTMLLinkElement>(
    'script[src*="/_next/"],link[href*="/_next/"]'
  );
  const nextAssetUrl = nextAsset?.getAttribute("src") ?? nextAsset?.getAttribute("href");
  if (nextAssetUrl) {
    const nextAssetPath = new URL(nextAssetUrl, window.location.origin).pathname;
    const nextMarkerIndex = nextAssetPath.indexOf("/_next/");
    if (nextMarkerIndex >= 0) {
      const basePath = nextAssetPath.slice(0, nextMarkerIndex);
      return new URL(`${basePath}/version.json`, window.location.origin).href;
    }
  }

  return new URL("/version.json", window.location.origin).href;
}

export function BuildIdentity() {
  const [info, setInfo] = useState<VersionInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    fetch(getBuildIdentityUrl(), { cache: "no-store", signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<VersionInfo>;
      })
      .then((data) => {
        if (!controller.signal.aborted) {
          setInfo(data);
        }
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        setError(e instanceof Error ? e.message : "Failed to load build info");
      });

    return () => controller.abort();
  }, []);

  if (error) {
    return (
      <p className="text-muted-foreground text-xs" data-testid="build-identity-error">
        Build identity unavailable: {error}
      </p>
    );
  }

  if (!info) {
    return (
      <p
        className="text-muted-foreground animate-pulse text-xs"
        data-testid="build-identity-loading"
      >
        Loading build info…
      </p>
    );
  }

  const builtAtDate = new Date(info.builtAt);
  const formattedDate = Number.isNaN(builtAtDate.getTime())
    ? info.builtAt
    : builtAtDate.toLocaleString();

  return (
    <p className="text-muted-foreground font-mono text-xs" data-testid="build-identity">
      Build{" "}
      <span className="text-foreground font-medium" data-testid="build-hash">
        {info.buildHash}
      </span>
      {" · "}
      <span data-testid="built-at">{formattedDate}</span>
    </p>
  );
}
