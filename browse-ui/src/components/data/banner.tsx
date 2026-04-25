import { AlertCircle, AlertTriangle, CheckCircle2, Info } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type BannerTone = "info" | "success" | "warning" | "danger";

const TONE_STYLES: Record<BannerTone, string> = {
  info: "border-[hsl(var(--chart-1)/0.3)] bg-[hsl(var(--chart-1)/0.1)] text-foreground",
  success:
    "border-[hsl(145_77%_24%/0.3)] bg-[hsl(142_72%_91%/0.35)] text-foreground dark:bg-[hsl(154_64%_8%/0.8)]",
  warning:
    "border-[hsl(45_78%_42%/0.3)] bg-[hsl(48_100%_90%/0.55)] text-foreground dark:bg-[hsl(45_58%_10%/0.8)]",
  danger:
    "border-destructive/30 bg-destructive/10 text-foreground dark:bg-destructive/20",
};

const TONE_ICONS: Record<BannerTone, ReactNode> = {
  info: <Info className="size-4" aria-hidden />,
  success: <CheckCircle2 className="size-4" aria-hidden />,
  warning: <AlertTriangle className="size-4" aria-hidden />,
  danger: <AlertCircle className="size-4" aria-hidden />,
};

type BannerProps = {
  title: string;
  description?: string;
  tone?: BannerTone;
  className?: string;
  actions?: ReactNode;
};

export function Banner({
  title,
  description,
  tone = "info",
  className,
  actions,
}: BannerProps) {
  return (
    <div
      role="status"
      className={cn(
        "flex items-start gap-3 rounded-lg border px-3 py-2",
        TONE_STYLES[tone],
        className
      )}
    >
      <div className="pt-0.5 text-muted-foreground">{TONE_ICONS[tone]}</div>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{title}</p>
        {description ? (
          <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="shrink-0">{actions}</div> : null}
    </div>
  );
}
