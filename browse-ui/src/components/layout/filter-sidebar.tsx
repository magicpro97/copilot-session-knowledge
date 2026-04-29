"use client";

import { ChevronDown, ChevronRight, Filter } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

type FilterSection = {
  id: string;
  title: string;
  content: ReactNode;
  defaultOpen?: boolean;
};

type FilterSidebarProps = {
  title?: string;
  sections: FilterSection[];
  className?: string;
  collapsible?: boolean;
};

export function FilterSidebar({
  title = "Filters",
  sections,
  className,
  collapsible = true,
}: FilterSidebarProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [openSectionIds, setOpenSectionIds] = useState<Record<string, boolean>>(
    Object.fromEntries(sections.map((section) => [section.id, section.defaultOpen !== false]))
  );

  if (collapsed) {
    return (
      <aside className={cn("bg-card w-12 shrink-0 rounded-xl border p-2", className)}>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Expand filters"
          onClick={() => setCollapsed(false)}
        >
          <Filter className="size-4" />
        </Button>
      </aside>
    );
  }

  return (
    <aside className={cn("bg-card w-[var(--sidebar-width)] shrink-0 rounded-xl border", className)}>
      <div className="flex items-center justify-between border-b px-3 py-2">
        <h2 className="text-sm font-medium">{title}</h2>
        {collapsible ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Collapse filters"
            onClick={() => setCollapsed(true)}
          >
            <ChevronRight className="size-4 rotate-180" />
          </Button>
        ) : null}
      </div>

      <ScrollArea className="h-[calc(100vh-10rem)]">
        <div className="space-y-1 p-2">
          {sections.map((section) => {
            const isOpen = openSectionIds[section.id];
            return (
              <section key={section.id} className="border-border/80 rounded-lg border">
                <button
                  type="button"
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium"
                  onClick={() =>
                    setOpenSectionIds((prev) => ({
                      ...prev,
                      [section.id]: !prev[section.id],
                    }))
                  }
                >
                  <span>{section.title}</span>
                  {isOpen ? (
                    <ChevronDown className="text-muted-foreground size-4" />
                  ) : (
                    <ChevronRight className="text-muted-foreground size-4" />
                  )}
                </button>
                {isOpen ? (
                  <div className="border-t px-3 py-2 text-sm">{section.content}</div>
                ) : null}
              </section>
            );
          })}
        </div>
      </ScrollArea>
    </aside>
  );
}
