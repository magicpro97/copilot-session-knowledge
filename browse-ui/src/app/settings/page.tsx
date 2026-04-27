"use client";

import { Activity, AlertTriangle, CheckCircle2, Copy, Moon, Monitor, Sun } from "lucide-react";
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
import { useHealth, useSyncStatus } from "@/lib/api/hooks";
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

async function copyCommand(command: string) {
  if (!command || typeof navigator === "undefined" || !navigator.clipboard) return;
  try {
    await navigator.clipboard.writeText(command);
  } catch (_error) {
    // Ignore clipboard failures in unsupported environments.
  }
}

export default function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const [density] = useDensity();
  const health = useHealth();
  const syncStatus = useSyncStatus();

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
          <CardTitle>Sync diagnostics</CardTitle>
          <CardDescription>
            Read-only sync diagnostics from <code>/api/sync/status</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {syncStatus.isLoading ? (
            <div className="grid gap-2 sm:grid-cols-4">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : null}

          {syncStatus.isError ? (
            <Banner
              tone="warning"
              title="Sync diagnostics unavailable"
              description={
                syncStatus.error instanceof Error
                  ? syncStatus.error.message
                  : "Could not fetch /api/sync/status."
              }
              actions={
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => syncStatus.refetch()}
                >
                  Retry
                </Button>
              }
            />
          ) : null}

          {syncStatus.data ? (
            <>
              <div className="grid gap-2 sm:grid-cols-4">
                <div className="rounded-lg border bg-card p-3">
                  <p className="text-xs text-muted-foreground">Mode</p>
                  <p className="mt-1 text-sm font-medium">{syncStatus.data.status}</p>
                </div>
                <div className="rounded-lg border bg-card p-3">
                  <p className="text-xs text-muted-foreground">Replica</p>
                  <p className="mt-1 text-sm font-medium">
                    {syncStatus.data.local_replica_id ?? "Not set"}
                  </p>
                </div>
                <div className="rounded-lg border bg-card p-3">
                  <p className="text-xs text-muted-foreground">Pending txns</p>
                  <p className="mt-1 text-sm font-medium">{formatNumber(syncStatus.data.pending_txns)}</p>
                </div>
                <div className="rounded-lg border bg-card p-3">
                  <p className="text-xs text-muted-foreground">Failed ops</p>
                  <p className="mt-1 flex items-center gap-2 text-sm font-medium">
                    {syncStatus.data.failed_ops > 0 ? (
                      <AlertTriangle className="size-3.5 text-amber-500" />
                    ) : (
                      <CheckCircle2 className="size-3.5 text-emerald-500" />
                    )}
                    {formatNumber(syncStatus.data.failed_ops)}
                  </p>
                </div>
              </div>

              <div className="space-y-1 rounded-lg border bg-card p-3 text-xs text-muted-foreground">
                <p>
                  Gateway:{" "}
                  <span className="font-medium text-foreground">
                    {syncStatus.data.connection.endpoint ?? "Not configured (local-only mode)"}
                  </span>
                </p>
                <p>
                  Target:{" "}
                  <span className="font-medium text-foreground">
                    {syncStatus.data.connection.target ?? "unconfigured"}
                  </span>
                </p>
                <p>
                  Config file:{" "}
                  <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
                    {syncStatus.data.connection.config_path}
                  </code>
                </p>
              </div>

              <Banner
                tone="info"
                title="Rollout contract"
                description={
                  syncStatus.data.rollout
                    ? `Client stays local-first and syncs via HTTP(S) gateway URL. Direct Postgres/libSQL sync in CLI core: ${syncStatus.data.rollout.direct_db_sync ? "yes" : "no"}.`
                    : "Client stays local-first and syncs via HTTP(S) gateway URL. Direct Postgres/libSQL sync in CLI core is not part of this batch."
                }
              />

              <div className="space-y-1 rounded-lg border bg-card p-3 text-xs text-muted-foreground">
                <p className="font-medium text-foreground">Gateway paths</p>
                <p>
                  Reference/mock:{" "}
                  {syncStatus.data.rollout?.reference_gateway.description ??
                    "In-repo reference/mock gateway for local integration tests."}
                </p>
                <p>
                  Provider-backed:{" "}
                  {syncStatus.data.rollout?.provider_gateway.description ??
                    "Deploy a thin provider-backed HTTP gateway and set its HTTPS URL in sync-config."}
                </p>
              </div>

              <div className="space-y-1 rounded-lg border bg-card p-3 text-xs text-muted-foreground">
                <p className="font-medium text-foreground">Runtime visibility</p>
                <p>
                  DB mode:{" "}
                  <span className="font-medium text-foreground">{syncStatus.data.runtime.db_mode}</span>
                </p>
                <p>
                  Sync tables ready:{" "}
                  <span className="font-medium text-foreground">
                    {syncStatus.data.runtime.available_sync_tables}/
                    {syncStatus.data.runtime.total_sync_tables}
                  </span>
                </p>
                <p>
                  Runtime failures (txns):{" "}
                  <span className="font-medium text-foreground">
                    {formatNumber(syncStatus.data.failed_txns)}
                  </span>
                </p>
                <p>
                  Runtime snapshot:{" "}
                  <span className="font-medium text-foreground">
                    {syncStatus.data.runtime.generated_at}
                  </span>
                </p>
                <p>
                  DB path:{" "}
                  <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
                    {syncStatus.data.runtime.db_path}
                  </code>
                </p>
              </div>

              <div className="space-y-2 rounded-lg border bg-card p-3">
                <p className="text-xs font-medium text-foreground">Operator checks (read-only)</p>
                <p className="text-xs text-muted-foreground">
                  Safe command-line checks for local visibility only. No write operations are listed.
                </p>
                <div className="space-y-2">
                  {syncStatus.data.operator_actions.map((action) => (
                    <div key={action.id} className="rounded-md border bg-background p-2 text-xs">
                      <p className="font-medium text-foreground">{action.title}</p>
                      <p className="text-muted-foreground">{action.description}</p>
                      <div className="mt-1 flex flex-wrap items-center gap-2">
                        <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
                          {action.command}
                        </code>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-6 px-2 text-[11px]"
                          onClick={() => void copyCommand(action.command)}
                        >
                          <Copy className="size-3" />
                          Copy
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          ) : null}
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
