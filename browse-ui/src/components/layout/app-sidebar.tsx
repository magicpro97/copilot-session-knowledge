"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Search,
  ScrollText,
  BarChart3,
  Network,
  Settings,
  PanelLeftClose,
  PanelLeftOpen,
  MessageSquare,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useResolvedPathname } from "@/hooks/use-resolved-pathname";
import { matchesAppPath } from "@/lib/pathname";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/sessions", label: "Sessions", icon: ScrollText },
  { href: "/search", label: "Search", icon: Search },
  { href: "/insights", label: "Insights", icon: BarChart3 },
  { href: "/graph", label: "Graph", icon: Network },
  { href: "/settings", label: "Settings", icon: Settings },
];

const SIDEBAR_COLLAPSED_KEY = "browse-sidebar-collapsed";

export function AppSidebar() {
  const pathname = useResolvedPathname();
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const saved = window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY);
    if (saved === "1") setCollapsed(true);
  }, []);

  useEffect(() => {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "b") return;
      if (event.defaultPrevented) return;
      if (
        event.target instanceof HTMLElement &&
        event.target.closest("input, textarea, select, [contenteditable='true']")
      )
        return;

      event.preventDefault();
      setCollapsed((prev) => !prev);
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <aside
      className="bg-card flex h-screen shrink-0 flex-col border-r transition-[width] duration-200 motion-reduce:transition-none"
      style={{ width: collapsed ? "var(--sidebar-rail-width,4rem)" : "var(--sidebar-width,16rem)" }}
    >
      <div
        className={cn(
          "flex h-14 items-center border-b px-2",
          collapsed ? "justify-center" : "justify-between px-4"
        )}
      >
        <Link
          href="/sessions"
          title="Go to Sessions"
          className={cn(
            "text-primary text-base font-semibold",
            collapsed && "rounded-md px-2 py-1"
          )}
        >
          <span className={cn(collapsed && "sr-only")}>Hindsight</span>
          {collapsed ? <span aria-hidden>H</span> : null}
        </Link>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={cn("size-8", collapsed && "hidden")}
          onClick={() => setCollapsed((prev) => !prev)}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar (⌘/Ctrl+B)" : "Collapse sidebar (⌘/Ctrl+B)"}
        >
          <PanelLeftClose className="size-4" />
        </Button>
      </div>
      {collapsed ? (
        <div className="border-b px-2 py-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-8"
            onClick={() => setCollapsed(false)}
            aria-label="Expand sidebar"
            title="Expand sidebar (⌘/Ctrl+B)"
          >
            <PanelLeftOpen className="size-4" />
          </Button>
        </div>
      ) : null}
      <nav className="flex-1 space-y-1 p-2">
        {navItems.map(({ href, label, icon: Icon }) => {
          const active = matchesAppPath(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              title={collapsed ? label : undefined}
              aria-label={label}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                collapsed && "justify-center px-2",
                active
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              <span className={cn("truncate", collapsed && "sr-only")}>{label}</span>
            </Link>
          );
        })}
      </nav>
      <div className={cn("text-muted-foreground border-t p-2 text-xs", collapsed && "px-1")}>
        <div className={cn("bg-muted/40 rounded-md px-2 py-1", collapsed && "text-center")}>
          {collapsed ? "⌘B" : "⌘/Ctrl+B Toggle rail"}
        </div>
      </div>
    </aside>
  );
}
