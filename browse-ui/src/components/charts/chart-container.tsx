import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type ChartContainerProps = {
  title?: string;
  description?: string;
  className?: string;
  children: ReactNode;
  actions?: ReactNode;
};

export function ChartContainer({
  title,
  description,
  className,
  children,
  actions,
}: ChartContainerProps) {
  return (
    <section className={cn("bg-card rounded-xl border p-4", className)}>
      {title || description || actions ? (
        <header className="mb-3 flex items-start justify-between gap-3">
          <div>
            {title ? <h3 className="text-sm font-medium">{title}</h3> : null}
            {description ? <p className="text-muted-foreground text-sm">{description}</p> : null}
          </div>
          {actions ? <div className="shrink-0">{actions}</div> : null}
        </header>
      ) : null}
      <div className="h-72 w-full">{children}</div>
    </section>
  );
}
