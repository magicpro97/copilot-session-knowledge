"use client";

import { useMemo } from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { Breadcrumbs, type BreadcrumbItem } from "@/components/layout/breadcrumbs";
import { Button } from "@/components/ui/button";
import { useResolvedPathname } from "@/hooks/use-resolved-pathname";
import { normalizeAppPathname } from "@/lib/pathname";

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

export function Header() {
  const pathname = useResolvedPathname();
  const { theme, setTheme } = useTheme();
  const routeContext = useMemo(() => getRouteContext(normalizeAppPathname(pathname)), [pathname]);

  return (
    <header className="bg-card flex h-14 items-center justify-between gap-4 border-b px-4">
      <div className="flex min-w-0 items-center gap-3">
        <Breadcrumbs items={routeContext.breadcrumbs} className="min-w-0 text-xs" />
        <span className="text-border hidden lg:inline">•</span>
        <p className="text-muted-foreground hidden truncate text-xs lg:block">
          {routeContext.subtitle}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <div className="text-muted-foreground hidden items-center gap-1 text-xs sm:flex">
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
          <Sun className="h-4 w-4 scale-100 rotate-0 transition-all dark:scale-0 dark:-rotate-90" />
          <Moon className="absolute h-4 w-4 scale-0 rotate-90 transition-all dark:scale-100 dark:rotate-0" />
        </Button>
      </div>
    </header>
  );
}
