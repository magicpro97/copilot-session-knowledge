import Link from "next/link";
import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

export type BreadcrumbItem = {
  label: string;
  href?: string;
};

type BreadcrumbsProps = {
  items: BreadcrumbItem[];
  className?: string;
};

export function Breadcrumbs({ items, className }: BreadcrumbsProps) {
  if (items.length === 0) return null;

  return (
    <nav aria-label="Breadcrumb" className={cn("text-sm", className)}>
      <ol className="text-muted-foreground flex flex-wrap items-center gap-1">
        {items.map((item, index) => {
          const isLast = index === items.length - 1;
          return (
            <li key={`${item.label}-${index}`} className="flex min-w-0 items-center gap-1">
              {item.href && !isLast ? (
                <Link href={item.href} className="hover:text-foreground truncate">
                  {item.label}
                </Link>
              ) : (
                <span
                  aria-current={isLast ? "page" : undefined}
                  className={cn("truncate", isLast ? "text-foreground font-medium" : undefined)}
                >
                  {item.label}
                </span>
              )}
              {!isLast ? <ChevronRight className="size-3.5" aria-hidden /> : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
