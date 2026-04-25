"use client";

import { Activity, CheckCircle2, Moon, Monitor, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { Banner } from "@/components/data/banner";
import { DensityToggle } from "@/components/layout/density-toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useDensity } from "@/hooks/use-density";
import { useHealth } from "@/lib/api/hooks";
import { SHORTCUT_GROUPS } from "@/lib/constants";
import { formatNumber } from "@/lib/formatters";
import { cn } from "@/lib/utils";

const THEME_OPTIONS = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const;

function statusTone(status: string | undefined): "success" | "warning" {
  if (!status) return "warning";
  return status.toLowerCase().includes("ok") ? "success" : "warning";
}

export default function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const [density] = useDensity();
  const health = useHealth();

  const activeTheme = theme ?? "system";
  const healthStatus = health.data?.status;

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-muted-foreground">
          Manage interface preferences and review runtime diagnostics.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Appearance & preferences</CardTitle>
          <CardDescription>
            Theme and density are persisted in the browser and applied immediately.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <p className="text-sm font-medium">Theme</p>
            <div className="flex flex-wrap gap-2">
              {THEME_OPTIONS.map(({ value, label, icon: Icon }) => (
                <Button
                  key={value}
                  type="button"
                  variant={activeTheme === value ? "secondary" : "outline"}
                  size="sm"
                  onClick={() => setTheme(value)}
                  aria-pressed={activeTheme === value}
                >
                  <Icon className="size-3.5" />
                  {label}
                </Button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-sm font-medium">Density</p>
            <DensityToggle />
            <p className="text-xs text-muted-foreground">
              Current density:{" "}
              <span className="font-medium text-foreground">{density}</span> (stored as{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
                browse-density
              </code>
              ).
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>System health</CardTitle>
          <CardDescription>
            Diagnostics are read directly from <code>/healthz</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {health.isLoading ? (
            <div className="grid gap-2 sm:grid-cols-3">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : null}

          {health.isError ? (
            <Banner
              tone="danger"
              title="Health endpoint unavailable"
              description={
                health.error instanceof Error
                  ? health.error.message
                  : "Could not fetch /healthz."
              }
              actions={
                <Button type="button" variant="outline" size="sm" onClick={() => health.refetch()}>
                  Retry
                </Button>
              }
            />
          ) : null}

          {health.data ? (
            <>
              <div className="grid gap-2 sm:grid-cols-3">
                <div className="rounded-lg border bg-card p-3">
                  <p className="text-xs text-muted-foreground">Status</p>
                  <p className="mt-1 flex items-center gap-2 text-sm font-medium">
                    <Activity
                      className={cn(
                        "size-3.5",
                        statusTone(healthStatus) === "success"
                          ? "text-[hsl(142_72%_38%)]"
                          : "text-[hsl(45_80%_45%)]"
                      )}
                    />
                    {health.data.status}
                  </p>
                </div>
                <div className="rounded-lg border bg-card p-3">
                  <p className="text-xs text-muted-foreground">Schema version</p>
                  <p className="mt-1 text-sm font-medium">v{health.data.schema_version}</p>
                </div>
                <div className="rounded-lg border bg-card p-3">
                  <p className="text-xs text-muted-foreground">Indexed sessions</p>
                  <p className="mt-1 text-sm font-medium">{formatNumber(health.data.sessions)}</p>
                </div>
              </div>

              <Banner
                tone="info"
                title="Backend diagnostics are intentionally minimal"
                description="Current contract exposes status, schema version, and session count only. Database path and last-indexed timestamp are not available from /healthz."
              />
            </>
          ) : null}
        </CardContent>
      </Card>

      <Card id="shortcuts">
        <CardHeader>
          <CardTitle>Keyboard shortcuts reference</CardTitle>
          <CardDescription>
            Read-only list of shortcuts and interactions currently implemented in the UI.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {SHORTCUT_GROUPS.map((group) => (
            <div key={group.title} className="space-y-2">
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-medium">{group.title}</h2>
                <Badge variant="outline">{group.items.length}</Badge>
              </div>
              <div className="space-y-1 rounded-lg border">
                {group.items.map((item) => (
                  <div
                    key={`${group.title}-${item.keys}-${item.action}`}
                    className="flex flex-col justify-between gap-2 px-3 py-2 text-sm sm:flex-row sm:items-center"
                  >
                    <kbd className="w-fit rounded border bg-muted px-1.5 py-0.5 font-mono text-xs">
                      {item.keys}
                    </kbd>
                    <span className="text-muted-foreground">{item.action}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}

          <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs text-muted-foreground">
            <CheckCircle2 className="size-3.5 text-emerald-500" />
            Most shortcuts are disabled while typing in input fields.
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
