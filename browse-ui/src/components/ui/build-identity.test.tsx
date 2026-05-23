import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, afterEach } from "vitest";

// Dynamic import to handle "use client"
const { BuildIdentity } = await import("@/components/ui/build-identity");

describe("BuildIdentity", () => {
  afterEach(() => {
    document.querySelectorAll("[data-build-identity-test]").forEach((element) => element.remove());
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows loading state initially", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})) // never resolves
    );
    render(<BuildIdentity />);
    expect(screen.getByTestId("build-identity-loading")).toBeInTheDocument();
  });

  it("renders build hash and builtAt after successful fetch", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            buildHash: "abc1234",
            builtAt: "2026-01-15T12:00:00.000Z",
            version: "1.2.3",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );

    render(<BuildIdentity />);

    await waitFor(() => {
      expect(screen.getByTestId("build-identity")).toBeInTheDocument();
    });
    expect(screen.getByTestId("build-hash")).toHaveTextContent("abc1234");
    expect(screen.getByTestId("built-at")).toBeInTheDocument();
  });

  it("renders the original builtAt value when it is not a valid date", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            buildHash: "abc1234",
            builtAt: "not-a-date",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );

    render(<BuildIdentity />);

    await waitFor(() => {
      expect(screen.getByTestId("build-identity")).toBeInTheDocument();
    });

    expect(screen.getByTestId("built-at")).toHaveTextContent("not-a-date");
  });

  it("shows error state when fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Network error")));

    render(<BuildIdentity />);

    await waitFor(() => {
      expect(screen.getByTestId("build-identity-error")).toBeInTheDocument();
    });

    expect(screen.getByTestId("build-identity-error")).toHaveTextContent(
      "Build identity unavailable: Network error"
    );
  });

  it("shows error state when fetch returns non-ok response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Not Found", { status: 404 })));

    render(<BuildIdentity />);

    await waitFor(() => {
      expect(screen.getByTestId("build-identity-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("build-identity-error")).toHaveTextContent("HTTP 404");
  });

  it("fetches an absolute version.json URL with cache: no-store", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ buildHash: "deadbeef", builtAt: "2026-01-01T00:00:00.000Z" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );
    vi.stubGlobal("fetch", mockFetch);

    render(<BuildIdentity />);

    await waitFor(() => {
      expect(screen.getByTestId("build-identity")).toBeInTheDocument();
    });

    expect(mockFetch).toHaveBeenCalledWith(new URL("/version.json", window.location.origin).href, {
      cache: "no-store",
      signal: expect.any(AbortSignal),
    });
  });

  it("resolves version.json from the Next.js basePath when static assets are prefixed", async () => {
    const script = document.createElement("script");
    script.dataset.buildIdentityTest = "true";
    script.src = "/v2/_next/static/chunks/app.js";
    document.head.append(script);

    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ buildHash: "feedbee", builtAt: "2026-01-01T00:00:00.000Z" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", mockFetch);

    render(<BuildIdentity />);

    await waitFor(() => {
      expect(screen.getByTestId("build-identity")).toBeInTheDocument();
    });

    expect(mockFetch).toHaveBeenCalledWith(
      new URL("/v2/version.json", window.location.origin).href,
      {
        cache: "no-store",
        signal: expect.any(AbortSignal),
      }
    );
  });

  it("resolves version.json from an explicit document base URL", async () => {
    const base = document.createElement("base");
    base.dataset.buildIdentityTest = "true";
    base.href = "/preview/";
    document.head.append(base);

    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ buildHash: "decaf00", builtAt: "2026-01-01T00:00:00.000Z" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", mockFetch);

    render(<BuildIdentity />);

    await waitFor(() => {
      expect(screen.getByTestId("build-identity")).toBeInTheDocument();
    });

    expect(mockFetch).toHaveBeenCalledWith(new URL("version.json", base.href).href, {
      cache: "no-store",
      signal: expect.any(AbortSignal),
    });
  });

  it("aborts the version fetch on unmount", () => {
    let signal: AbortSignal | undefined;
    const mockFetch = vi.fn((_url: string, init?: RequestInit) => {
      signal = init?.signal as AbortSignal | undefined;
      return new Promise(() => {});
    });
    vi.stubGlobal("fetch", mockFetch);

    const { unmount } = render(<BuildIdentity />);

    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });
});
