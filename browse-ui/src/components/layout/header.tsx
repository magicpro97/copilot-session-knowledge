"use client";

import { useMemo } from "react";
import { usePathname } from "next/navigation";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { Breadcrumbs, type BreadcrumbItem } from "@/components/layout/breadcrumbs";
import { Button } from "@/components/ui/button";

type RouteContext = {
  title: string;
  subtitle: string;
  breadcrumbs: BreadcrumbItem[];
};

function getRouteContext(pathname: string): RouteContext {
  if (pathname.startsWith("/sessions/")) {
    return {
      title: "Session detail",
      subtitle: "Inspect timeline, checkpoints, and exported context for one run.",
      breadcrumbs: [
        { label: "Sessions", href: "/sessions" },
        { label: "Session detail" },
      ],
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

export function Header() {
  const pathname = usePathname();
  const { theme, setTheme } = useTheme();
  const routeContext = useMemo(() => getRouteContext(pathname), [pathname]);

  return (
    <header className="flex h-14 items-center justify-between gap-4 border-b bg-card px-4">
      <div className="flex min-w-0 items-center gap-3">
        <Breadcrumbs items={routeContext.breadcrumbs} className="min-w-0 text-xs" />
        <span className="hidden text-border lg:inline">•</span>
        <p className="hidden truncate text-xs text-muted-foreground lg:block">
          {routeContext.subtitle}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <div className="hidden items-center gap-1 text-xs text-muted-foreground sm:flex">
          <kbd className="rounded border px-1 font-mono text-[10px]">⌘K</kbd>
          <span>command palette</span>
          <span className="text-border">•</span>
          <kbd className="rounded border px-1 font-mono text-[10px]">⌘B</kbd>
          <span>sidebar rail</span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          aria-label="Toggle theme"
        >
          <Sun className="h-4 w-4 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
          <Moon className="absolute h-4 w-4 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
        </Button>
      </div>
    </header>
  );
}
