"use client";

import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ExternalLink, Globe, Info, Moon, ServerCog, Sun } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { Breadcrumbs, type BreadcrumbItem } from "@/components/layout/breadcrumbs";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useResolvedPathname } from "@/hooks/use-resolved-pathname";
import { useKeyboardPlatform } from "@/hooks/use-keyboard-platform";
import { normalizeAppPathname } from "@/lib/pathname";
import {
  BROWSE_HOST_CHANGE_EVENT,
  LOCAL_HOST,
  LOCAL_HOST_ID,
  getAllHostProfiles,
  isLocalOrigin,
  setSelectedHostId,
} from "@/lib/host-profiles";
import { isLoopbackUrl } from "@/lib/http/loopback";
import { formatModShortcut } from "@/lib/shortcut-utils";
import type { HostProfile } from "@/lib/api/types";
import { useHostState } from "@/providers/host-provider";
import { cn } from "@/lib/utils";

type RouteContext = {
  title: string;
  subtitle: string;
  breadcrumbs: BreadcrumbItem[];
};

function getRouteContext(pathname: string): RouteContext {
  if (pathname === "/chat") {
    return {
      title: "Chat",
      subtitle: "Run Copilot CLI prompts against a workspace and inspect results.",
      breadcrumbs: [{ label: "Chat" }],
    };
  }

  if (pathname.startsWith("/sessions/")) {
    return {
      title: "Session detail",
      subtitle: "Inspect timeline, checkpoints, and exported context for one run.",
      breadcrumbs: [{ label: "Sessions", href: "/sessions" }, { label: "Session detail" }],
    };
  }

  if (pathname === "/search") {
    return {
      title: "Search",
      subtitle: "Query extracted knowledge and jump directly to matching sessions.",
      breadcrumbs: [{ label: "Search" }],
    };
  }

  if (pathname === "/insights") {
    return {
      title: "Insights",
      subtitle: "Track knowledge trends, live feed status, and evaluation health.",
      breadcrumbs: [{ label: "Insights" }],
    };
  }

  if (pathname === "/graph") {
    return {
      title: "Graph",
      subtitle: "Explore relationships and embedding clusters in one network workspace.",
      breadcrumbs: [{ label: "Graph" }],
    };
  }

  if (pathname === "/settings") {
    return {
      title: "Settings",
      subtitle: "Tune preferences, diagnostics, and shortcut references.",
      breadcrumbs: [{ label: "Settings" }],
    };
  }

  return {
    title: "Sessions",
    subtitle: "Review indexed sessions and drill into details quickly.",
    breadcrumbs: [{ label: "Sessions" }],
  };
}

/** Compact AWS-region-style label for the active host. */
function HostLabel({ host }: { host: HostProfile }) {
  const isLocal = host.id === LOCAL_HOST_ID;
  return (
    <span className="flex items-center gap-1.5">
      {isLocal ? (
        <ServerCog className="size-3.5 shrink-0" />
      ) : (
        <Globe className="size-3.5 shrink-0" />
      )}
      <span className="hidden max-w-[120px] truncate sm:inline">
        {isLocal ? "Local" : host.label}
      </span>
    </span>
  );
}

function CodePill({ children }: { children: ReactNode }) {
  return <code className="bg-muted rounded px-1 font-mono text-[11px]">{children}</code>;
}

function HostedBackendGuideBanner() {
  return (
    <div
      className="border-b border-blue-500/25 bg-blue-500/5 px-4 py-2 text-xs"
      data-testid="hosted-backend-guide-banner"
      role="region"
      aria-label="Local backend setup guide"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="flex items-center gap-1 font-medium text-blue-700 dark:text-blue-300">
          <Info className="size-3.5" aria-hidden="true" />
          Local backend
        </span>
        <span className="text-muted-foreground">
          Run <CodePill>browse --hosted-bootstrap --open-browser chrome</CodePill> to start the
          backend and open a configured browser.
        </span>
        <a
          href="https://127.0.0.1:8765/"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 font-medium text-blue-700 underline underline-offset-2 dark:text-blue-300"
          data-testid="hosted-backend-guide-open-https"
        >
          Open local app
          <ExternalLink className="size-3" aria-hidden="true" />
        </a>
        <span className="text-muted-foreground">
          no-TLS:{" "}
          <a
            href="http://127.0.0.1:8765/"
            target="_blank"
            rel="noreferrer"
            className="font-mono underline underline-offset-2"
            data-testid="hosted-backend-guide-open-http"
          >
            http://127.0.0.1:8765
          </a>
        </span>
      </div>
    </div>
  );
}

export function Header() {
  const pathname = useResolvedPathname();
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const routeContext = useMemo(() => getRouteContext(normalizeAppPathname(pathname)), [pathname]);

  // Shared browse-wide host state from HostProvider.
  const { host: activeHost } = useHostState();
  const [allHosts, setAllHosts] = useState<HostProfile[]>([LOCAL_HOST]);
  const [isHosted, setIsHosted] = useState(false);
  const platform = useKeyboardPlatform();

  useEffect(() => {
    setIsHosted(!isLocalOrigin(window.location.origin));
  }, []);

  useEffect(() => {
    const refresh = () => setAllHosts(getAllHostProfiles());
    refresh();
    window.addEventListener("storage", refresh);
    window.addEventListener(BROWSE_HOST_CHANGE_EVENT, refresh);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener(BROWSE_HOST_CHANGE_EVENT, refresh);
    };
  }, []);

  const activeHostIsLoopback = activeHost.base_url ? isLoopbackUrl(activeHost.base_url) : false;
  const showBackendGuide = isHosted && (activeHost.id === LOCAL_HOST_ID || activeHostIsLoopback);

  return (
    <>
      <header className="bg-card flex h-14 items-center justify-between gap-4 border-b px-4">
        <div className="flex min-w-0 items-center gap-3">
          <Breadcrumbs items={routeContext.breadcrumbs} className="min-w-0 text-xs" />
          <span className="text-border hidden lg:inline">•</span>
          <p className="text-muted-foreground hidden truncate text-xs lg:block">
            {routeContext.subtitle}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {/* AWS-region-style global host switcher */}
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 gap-1 px-2 text-xs"
                  aria-label={`Active host: ${activeHost.id === LOCAL_HOST_ID ? "Local" : activeHost.label}`}
                  data-testid="header-host-trigger"
                >
                  <HostLabel host={activeHost} />
                  <ChevronDown className="size-3 shrink-0 opacity-60" />
                </Button>
              }
            />
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuGroup>
                <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
                  Agent host
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                {allHosts.map((h) => (
                  <DropdownMenuItem
                    key={h.id}
                    className={cn(
                      "flex items-center gap-2 text-xs",
                      activeHost.id === h.id && "font-medium"
                    )}
                    onClick={() => setSelectedHostId(h.id)}
                    data-testid={`host-option-${h.id}`}
                  >
                    {h.id === LOCAL_HOST_ID ? (
                      <ServerCog className="text-muted-foreground size-3.5 shrink-0" />
                    ) : (
                      <Globe className="text-muted-foreground size-3.5 shrink-0" />
                    )}
                    <span className="flex-1 truncate">
                      {h.id === LOCAL_HOST_ID ? "Local (same-origin)" : h.label}
                    </span>
                    {activeHost.id === h.id && (
                      <span className="text-primary ml-auto shrink-0">✓</span>
                    )}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="text-muted-foreground text-xs"
                onClick={() => {
                  router.push("/settings#hosts");
                }}
              >
                Manage hosts…
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <div className="text-muted-foreground hidden items-center gap-1 text-xs sm:flex">
            <kbd className="rounded border px-1 font-mono text-[10px]">
              {formatModShortcut("K", platform)}
            </kbd>
            <span>command palette</span>
            <span className="text-border">•</span>
            <kbd className="rounded border px-1 font-mono text-[10px]">
              {formatModShortcut("B", platform)}
            </kbd>
            <span>sidebar rail</span>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            aria-label="Toggle theme"
          >
            <Sun className="h-4 w-4 scale-100 rotate-0 transition-all dark:scale-0 dark:-rotate-90" />
            <Moon className="absolute h-4 w-4 scale-0 rotate-90 transition-all dark:scale-100 dark:rotate-0" />
          </Button>
        </div>
      </header>

      {showBackendGuide ? <HostedBackendGuideBanner /> : null}
    </>
  );
}
