"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Search,
  ScrollText,
  BarChart3,
  Network,
  Settings,
} from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/sessions", label: "Sessions", icon: ScrollText },
  { href: "/search", label: "Search", icon: Search },
  { href: "/insights", label: "Insights", icon: BarChart3 },
  { href: "/graph", label: "Graph", icon: Network },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function AppSidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex h-screen w-[var(--sidebar-width,16rem)] flex-col border-r bg-card">
      <div className="flex h-14 items-center border-b px-4">
        <Link href="/sessions" className="text-base font-semibold text-primary">
          Hindsight
        </Link>
      </div>
      <nav className="flex-1 space-y-1 p-2">
        {navItems.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
