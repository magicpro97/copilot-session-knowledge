"use client";

import { Activity, AlertTriangle, CheckCircle2, Moon, Monitor, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { useTheme } from "next-themes";

import { Banner } from "@/components/data/banner";
import { DensityToggle } from "@/components/layout/density-toggle";
import { OperatorActionsPanel } from "@/components/data/operator-actions-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useDensity } from "@/hooks/use-density";
import {
  useHealth,
  useScoutStatus,
  useSkillMetrics,
  useSyncStatus,
  useTentacleStatus,
} from "@/lib/api/hooks";
import type { HostProfile } from "@/lib/api/types";
import { SHORTCUT_GROUPS } from "@/lib/constants";
import { formatNumber } from "@/lib/formatters";
import { LOCAL_HOST, getEffectiveHost } from "@/lib/host-profiles";
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

  // Track the effective host and whether diagnostics requests are safe to fire.
  const [host, setHost] = useState<HostProfile>(LOCAL_HOST);
  const [diagnosticsEnabled, setDiagnosticsEnabled] = useState(false);

  useEffect(() => {
    const update = () => {
      const h = getEffectiveHost();
      setHost(h);
      setDiagnosticsEnabled(
        h.base_url !== "" ||
          window.location.pathname.startsWith("/v2") ||
          Boolean(process.env.NEXT_PUBLIC_API_BASE)
      );
    };
    update();
    window.addEventListener("storage", update);
    return () => window.removeEventListener("storage", update);
  }, []);

  const health = useHealth(host, diagnosticsEnabled);
  const syncStatus = useSyncStatus(host, diagnosticsEnabled);
  const scoutStatus = useScoutStatus(host, diagnosticsEnabled);
  const tentacleStatus = useTentacleStatus(host, diagnosticsEnabled);
  const skillMetrics = useSkillMetrics(host, diagnosticsEnabled);

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
            <p className="text-muted-foreground text-xs">
              Current density: <span className="text-foreground font-medium">{density}</span>{" "}
              (stored as{" "}
              <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
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
          {!diagnosticsEnabled ? (
            <p className="text-muted-foreground text-sm" data-testid="sync-diagnostics-idle">
              Select an agent host in{" "}
              <span className="text-foreground font-medium">Hosts &amp; connections</span> to view
              live sync diagnostics.
            </p>
          ) : (
            <>
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
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Mode</p>
                      <p className="mt-1 text-sm font-medium">{syncStatus.data.status}</p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Replica</p>
                      <p className="mt-1 text-sm font-medium">
                        {syncStatus.data.local_replica_id ?? "Not set"}
                      </p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Pending txns</p>
                      <p className="mt-1 text-sm font-medium">
                        {formatNumber(syncStatus.data.pending_txns)}
                      </p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Failed ops</p>
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

                  <div className="bg-card text-muted-foreground space-y-1 rounded-lg border p-3 text-xs">
                    <p>
                      Gateway:{" "}
                      <span className="text-foreground font-medium">
                        {syncStatus.data.connection.endpoint ?? "Not configured (local-only mode)"}
                      </span>
                    </p>
                    <p>
                      Target:{" "}
                      <span className="text-foreground font-medium">
                        {syncStatus.data.connection.target ?? "unconfigured"}
                      </span>
                    </p>
                    <p>
                      Config file:{" "}
                      <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
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

                  <div className="bg-card text-muted-foreground space-y-1 rounded-lg border p-3 text-xs">
                    <p className="text-foreground font-medium">Gateway paths</p>
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

                  <div className="bg-card text-muted-foreground space-y-1 rounded-lg border p-3 text-xs">
                    <p className="text-foreground font-medium">Runtime visibility</p>
                    <p>
                      DB mode:{" "}
                      <span className="text-foreground font-medium">
                        {syncStatus.data.runtime.db_mode}
                      </span>
                    </p>
                    <p>
                      Sync tables ready:{" "}
                      <span className="text-foreground font-medium">
                        {syncStatus.data.runtime.available_sync_tables}/
                        {syncStatus.data.runtime.total_sync_tables}
                      </span>
                    </p>
                    <p>
                      Runtime failures (txns):{" "}
                      <span className="text-foreground font-medium">
                        {formatNumber(syncStatus.data.failed_txns)}
                      </span>
                    </p>
                    <p>
                      Runtime snapshot:{" "}
                      <span className="text-foreground font-medium">
                        {syncStatus.data.runtime.generated_at}
                      </span>
                    </p>
                    <p>
                      DB path:{" "}
                      <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
                        {syncStatus.data.runtime.db_path}
                      </code>
                    </p>
                  </div>

                  <OperatorActionsPanel
                    actions={syncStatus.data.operator_actions}
                    note="Safe command-line checks for local visibility only. No write operations are listed."
                  />
                </>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Trend Scout diagnostics</CardTitle>
          <CardDescription>
            Read-only Trend Scout diagnostics from <code>/api/scout/status</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!diagnosticsEnabled ? (
            <p className="text-muted-foreground text-sm" data-testid="scout-diagnostics-idle">
              Select an agent host in{" "}
              <span className="text-foreground font-medium">Hosts &amp; connections</span> to view
              live Trend Scout diagnostics.
            </p>
          ) : (
            <>
              {scoutStatus.isLoading ? (
                <div className="grid gap-2 sm:grid-cols-4">
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                </div>
              ) : null}

              {scoutStatus.isError ? (
                <Banner
                  tone="warning"
                  title="Trend Scout diagnostics unavailable"
                  description={
                    scoutStatus.error instanceof Error
                      ? scoutStatus.error.message
                      : "Could not fetch /api/scout/status."
                  }
                  actions={
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => scoutStatus.refetch()}
                    >
                      Retry
                    </Button>
                  }
                />
              ) : null}

              {scoutStatus.data ? (
                <>
                  <div className="grid gap-2 sm:grid-cols-4">
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Mode</p>
                      <p className="mt-1 text-sm font-medium">{scoutStatus.data.status}</p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Target repo</p>
                      <p className="mt-1 text-sm font-medium">
                        {scoutStatus.data.config.target_repo ?? "Not configured"}
                      </p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Grace window</p>
                      <p className="mt-1 text-sm font-medium">
                        {scoutStatus.data.grace_window.enabled
                          ? `${scoutStatus.data.grace_window.grace_window_hours}h`
                          : "Disabled"}
                      </p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Warning checks</p>
                      <p className="mt-1 flex items-center gap-2 text-sm font-medium">
                        {scoutStatus.data.audit.summary.warning_checks > 0 ? (
                          <AlertTriangle className="size-3.5 text-amber-500" />
                        ) : (
                          <CheckCircle2 className="size-3.5 text-emerald-500" />
                        )}
                        {formatNumber(scoutStatus.data.audit.summary.warning_checks)}
                      </p>
                    </div>
                  </div>

                  <div className="bg-card text-muted-foreground space-y-1 rounded-lg border p-3 text-xs">
                    <p>
                      Config file:{" "}
                      <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
                        {scoutStatus.data.config.config_path}
                      </code>
                    </p>
                    <p>
                      Script file:{" "}
                      <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
                        {scoutStatus.data.config.script_path}
                      </code>
                    </p>
                    <p>
                      Runtime snapshot:{" "}
                      <span className="text-foreground font-medium">
                        {scoutStatus.data.runtime.generated_at}
                      </span>
                    </p>
                  </div>

                  <div className="bg-card text-muted-foreground space-y-1 rounded-lg border p-3 text-xs">
                    <p className="text-foreground font-medium">Analysis preview</p>
                    <p>
                      Enabled:{" "}
                      <span className="text-foreground font-medium">
                        {scoutStatus.data.analysis.enabled ? "yes" : "no"}
                      </span>
                    </p>
                    <p>
                      Model:{" "}
                      <span className="text-foreground font-medium">
                        {scoutStatus.data.analysis.model}
                      </span>
                    </p>
                    <p>
                      Token env:{" "}
                      <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
                        {scoutStatus.data.analysis.token_env}
                      </code>
                    </p>
                    <p>
                      Token present:{" "}
                      <span className="text-foreground font-medium">
                        {scoutStatus.data.analysis.token_present ? "yes" : "no"}
                      </span>
                    </p>
                  </div>

                  <div className="bg-card text-muted-foreground space-y-1 rounded-lg border p-3 text-xs">
                    <p className="text-foreground font-medium">Grace-window diagnostics</p>
                    <p>
                      State file:{" "}
                      <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
                        {scoutStatus.data.grace_window.state_file}
                      </code>
                    </p>
                    <p>
                      Last run UTC:{" "}
                      <span className="text-foreground font-medium">
                        {scoutStatus.data.grace_window.last_run_utc ?? "No state recorded"}
                      </span>
                    </p>
                    <p>
                      Skip without force:{" "}
                      <span className="text-foreground font-medium">
                        {scoutStatus.data.grace_window.would_skip_without_force ? "yes" : "no"}
                      </span>
                    </p>
                    {scoutStatus.data.grace_window.reason ? (
                      <p>
                        Reason:{" "}
                        <span className="text-foreground font-medium">
                          {scoutStatus.data.grace_window.reason}
                        </span>
                      </p>
                    ) : null}
                  </div>

                  <div className="bg-card space-y-2 rounded-lg border p-3">
                    <p className="text-foreground text-xs font-medium">Audit checks</p>
                    <div className="space-y-2">
                      {scoutStatus.data.audit.checks.map((check) => (
                        <div key={check.id} className="bg-background rounded-md border p-2 text-xs">
                          <div className="flex items-center justify-between gap-2">
                            <p className="text-foreground font-medium">{check.title}</p>
                            <Badge variant={check.status === "ok" ? "outline" : "secondary"}>
                              {check.status}
                            </Badge>
                          </div>
                          <p className="text-muted-foreground">{check.detail}</p>
                        </div>
                      ))}
                    </div>
                  </div>

                  {scoutStatus.data.discovery_lanes &&
                  scoutStatus.data.discovery_lanes.length > 0 ? (
                    <div className="bg-card space-y-2 rounded-lg border p-3">
                      <p className="text-foreground text-xs font-medium">
                        Discovery lanes ({scoutStatus.data.discovery_lanes.length})
                      </p>
                      <p className="text-muted-foreground text-xs">
                        Multi-lane search runs language-specific and language-agnostic queries in
                        parallel to surface a broader candidate set.
                      </p>
                      <div className="space-y-2">
                        {scoutStatus.data.discovery_lanes.map((lane) => (
                          <div
                            key={lane.name}
                            className="bg-background rounded-md border p-2 text-xs"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <p className="text-foreground font-medium">{lane.name}</p>
                              <span className="text-muted-foreground">
                                {lane.language ?? "any language"}
                              </span>
                            </div>
                            <p className="text-muted-foreground">
                              {lane.keyword_count} keyword{lane.keyword_count !== 1 ? "s" : ""} ·{" "}
                              {lane.topic_count} topic{lane.topic_count !== 1 ? "s" : ""} · min{" "}
                              {lane.min_stars} ★
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  <OperatorActionsPanel
                    actions={scoutStatus.data.operator_actions}
                    note="Copy-only safe commands. Browser does not execute Trend Scout operations."
                  />
                </>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Tentacle runtime diagnostics</CardTitle>
          <CardDescription>
            Read-only tentacle runtime status from <code>/api/tentacles/status</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!diagnosticsEnabled ? (
            <p className="text-muted-foreground text-sm" data-testid="tentacle-diagnostics-idle">
              Select an agent host in{" "}
              <span className="text-foreground font-medium">Hosts &amp; connections</span> to view
              live tentacle diagnostics.
            </p>
          ) : (
            <>
              {tentacleStatus.isLoading ? (
                <div className="grid gap-2 sm:grid-cols-4">
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                </div>
              ) : null}

              {tentacleStatus.isError ? (
                <Banner
                  tone="warning"
                  title="Tentacle diagnostics unavailable"
                  description={
                    tentacleStatus.error instanceof Error
                      ? tentacleStatus.error.message
                      : "Could not fetch /api/tentacles/status."
                  }
                  actions={
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => tentacleStatus.refetch()}
                    >
                      Retry
                    </Button>
                  }
                />
              ) : null}

              {tentacleStatus.data ? (
                <>
                  <div className="grid gap-2 sm:grid-cols-4">
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Mode</p>
                      <p className="mt-1 text-sm font-medium">{tentacleStatus.data.status}</p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Active tentacles</p>
                      <p className="mt-1 text-sm font-medium">
                        {tentacleStatus.data.active_count} / {tentacleStatus.data.total_count}
                      </p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Worktrees prepared</p>
                      <p className="mt-1 text-sm font-medium">
                        {tentacleStatus.data.worktrees_prepared}
                      </p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Verification covered</p>
                      <p className="mt-1 flex items-center gap-2 text-sm font-medium">
                        {tentacleStatus.data.audit.summary.warning_checks > 0 ? (
                          <AlertTriangle className="size-3.5 text-amber-500" />
                        ) : (
                          <CheckCircle2 className="size-3.5 text-emerald-500" />
                        )}
                        {tentacleStatus.data.verification_covered}
                      </p>
                    </div>
                  </div>

                  {tentacleStatus.data.goal_aware_count !== undefined &&
                  tentacleStatus.data.goal_aware_count > 0 ? (
                    <div className="bg-card text-muted-foreground space-y-1 rounded-lg border p-3 text-xs">
                      <p className="text-foreground font-medium">Goal-loop diagnostics</p>
                      <p>
                        Goal-linked tentacles:{" "}
                        <span className="text-foreground font-medium">
                          {tentacleStatus.data.goal_aware_count}
                        </span>
                      </p>
                      {tentacleStatus.data.tentacles
                        .filter((t) => t.goal_id)
                        .map((t) => (
                          <p key={t.tentacle_id || t.name}>
                            <span className="text-foreground font-medium">{t.name}</span>
                            {" → "}
                            {t.goal_name ?? t.goal_id}
                            {t.goal_iteration !== undefined ? ` (iter ${t.goal_iteration})` : ""}
                          </p>
                        ))}
                    </div>
                  ) : null}

                  <div className="bg-card text-muted-foreground space-y-1 rounded-lg border p-3 text-xs">
                    <p className="text-foreground font-medium">Dispatch marker</p>
                    <p>
                      Active:{" "}
                      <span className="text-foreground font-medium">
                        {tentacleStatus.data.marker.active ? "yes" : "no"}
                      </span>
                    </p>
                    {tentacleStatus.data.marker.age_hours !== null ? (
                      <p>
                        Marker age:{" "}
                        <span className="text-foreground font-medium">
                          {tentacleStatus.data.marker.age_hours.toFixed(1)}h
                          {tentacleStatus.data.marker.stale ? " (stale)" : ""}
                        </span>
                      </p>
                    ) : null}
                    <p>
                      Marker path:{" "}
                      <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
                        {tentacleStatus.data.marker.path}
                      </code>
                    </p>
                    <p>
                      Runtime snapshot:{" "}
                      <span className="text-foreground font-medium">
                        {tentacleStatus.data.runtime.generated_at}
                      </span>
                    </p>
                  </div>

                  {tentacleStatus.data.tentacles.length > 0 ? (
                    <div className="bg-card space-y-2 rounded-lg border p-3">
                      <p className="text-foreground text-xs font-medium">Tentacle registry</p>
                      <div className="space-y-2">
                        {tentacleStatus.data.tentacles.map((t) => (
                          <div
                            key={t.tentacle_id || t.name}
                            className="bg-background rounded-md border p-2 text-xs"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <p className="text-foreground font-medium">{t.name}</p>
                              <Badge variant={t.status === "active" ? "default" : "outline"}>
                                {t.status}
                              </Badge>
                            </div>
                            {t.description ? (
                              <p className="text-muted-foreground">{t.description}</p>
                            ) : null}
                            <div className="text-muted-foreground mt-1 flex flex-wrap gap-2">
                              <span>
                                Worktree:{" "}
                                <span className="text-foreground font-medium">
                                  {t.worktree.prepared ? "prepared" : "not prepared"}
                                  {t.worktree.stale ? " (stale)" : ""}
                                </span>
                              </span>
                              <span>
                                Verification:{" "}
                                <span className="text-foreground font-medium">
                                  {t.verification.coverage_exists
                                    ? `${t.verification.passed}/${t.verification.total} passed`
                                    : "none"}
                                </span>
                              </span>
                              {t.terminal_status ? (
                                <span>
                                  Handoff:{" "}
                                  <span className="text-foreground font-medium">
                                    {t.terminal_status}
                                  </span>
                                </span>
                              ) : t.has_handoff ? (
                                <span>
                                  Handoff:{" "}
                                  <span className="text-foreground font-medium">written</span>
                                </span>
                              ) : null}
                              {t.skills.length > 0 ? (
                                <span>
                                  Skills:{" "}
                                  <span className="text-foreground font-medium">
                                    {t.skills.join(", ")}
                                  </span>
                                </span>
                              ) : null}
                              {t.goal_id ? (
                                <span>
                                  Goal:{" "}
                                  <span className="text-foreground font-medium">
                                    {t.goal_name ?? t.goal_id}
                                    {t.goal_iteration !== undefined
                                      ? ` (iter ${t.goal_iteration})`
                                      : ""}
                                  </span>
                                </span>
                              ) : null}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  <OperatorActionsPanel
                    actions={tentacleStatus.data.operator_actions}
                    note="Copy-only safe commands. Browser does not execute tentacle operations."
                  />
                </>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Skill outcome metrics</CardTitle>
          <CardDescription>
            Read-only skill outcome diagnostics from <code>/api/skills/metrics</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!diagnosticsEnabled ? (
            <p className="text-muted-foreground text-sm" data-testid="skill-diagnostics-idle">
              Select an agent host in{" "}
              <span className="text-foreground font-medium">Hosts &amp; connections</span> to view
              live skill metrics.
            </p>
          ) : (
            <>
              {skillMetrics.isLoading ? (
                <div className="grid gap-2 sm:grid-cols-4">
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                </div>
              ) : null}

              {skillMetrics.isError ? (
                <Banner
                  tone="warning"
                  title="Skill metrics unavailable"
                  description={
                    skillMetrics.error instanceof Error
                      ? skillMetrics.error.message
                      : "Could not fetch /api/skills/metrics."
                  }
                  actions={
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => skillMetrics.refetch()}
                    >
                      Retry
                    </Button>
                  }
                />
              ) : null}

              {skillMetrics.data ? (
                <>
                  <div className="grid gap-2 sm:grid-cols-4">
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Status</p>
                      <p className="mt-1 text-sm font-medium">{skillMetrics.data.status}</p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Total outcomes</p>
                      <p className="mt-1 text-sm font-medium">
                        {formatNumber(skillMetrics.data.summary.total_outcomes)}
                      </p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Pass rate</p>
                      <p className="mt-1 text-sm font-medium">
                        {skillMetrics.data.summary.pass_rate !== null
                          ? `${(skillMetrics.data.summary.pass_rate * 100).toFixed(0)}%`
                          : "—"}
                      </p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">With verification</p>
                      <p className="mt-1 flex items-center gap-2 text-sm font-medium">
                        {skillMetrics.data.audit.summary.warning_checks > 0 ? (
                          <AlertTriangle className="size-3.5 text-amber-500" />
                        ) : (
                          <CheckCircle2 className="size-3.5 text-emerald-500" />
                        )}
                        {formatNumber(skillMetrics.data.summary.outcomes_with_verification)}
                      </p>
                    </div>
                  </div>

                  <div className="bg-card text-muted-foreground space-y-1 rounded-lg border p-3 text-xs">
                    <p className="text-foreground font-medium">DB visibility</p>
                    <p>
                      DB path:{" "}
                      <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
                        {skillMetrics.data.db_path}
                      </code>
                    </p>
                    <p>
                      Tables ready:{" "}
                      <span className="text-foreground font-medium">
                        outcomes={skillMetrics.data.tables.tentacle_outcomes ? "yes" : "no"} skills=
                        {skillMetrics.data.tables.tentacle_outcome_skills ? "yes" : "no"} verif=
                        {skillMetrics.data.tables.tentacle_verifications ? "yes" : "no"}
                      </span>
                    </p>
                    <p>
                      Outcomes with skills:{" "}
                      <span className="text-foreground font-medium">
                        {formatNumber(skillMetrics.data.summary.outcomes_with_skills)}
                      </span>
                    </p>
                    <p>
                      Outcomes with worktree:{" "}
                      <span className="text-foreground font-medium">
                        {formatNumber(skillMetrics.data.summary.outcomes_with_worktree)}
                      </span>
                    </p>
                    <p>
                      Runtime snapshot:{" "}
                      <span className="text-foreground font-medium">
                        {skillMetrics.data.runtime.generated_at}
                      </span>
                    </p>
                  </div>

                  {skillMetrics.data.skill_usage.length > 0 ? (
                    <div className="bg-card space-y-2 rounded-lg border p-3">
                      <p className="text-foreground text-xs font-medium">Skill usage summary</p>
                      <div className="space-y-1">
                        {skillMetrics.data.skill_usage.map((s) => (
                          <div
                            key={s.skill_name}
                            className="flex items-center justify-between text-xs"
                          >
                            <span className="text-foreground font-medium">{s.skill_name}</span>
                            <Badge variant="outline">{s.usage_count}</Badge>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {skillMetrics.data.recent_outcomes.length > 0 ? (
                    <div className="bg-card space-y-2 rounded-lg border p-3">
                      <p className="text-foreground text-xs font-medium">Recent outcomes</p>
                      <div className="space-y-2">
                        {skillMetrics.data.recent_outcomes.map((o) => (
                          <div key={o.id} className="bg-background rounded-md border p-2 text-xs">
                            <div className="flex items-center justify-between gap-2">
                              <p className="text-foreground font-medium">{o.tentacle_name}</p>
                              <Badge
                                variant={o.outcome_status === "success" ? "outline" : "secondary"}
                              >
                                {o.outcome_status}
                              </Badge>
                            </div>
                            <div className="text-muted-foreground mt-1 flex flex-wrap gap-2">
                              <span>{o.recorded_at}</span>
                              {o.verification_total > 0 ? (
                                <span>
                                  Verification: {o.verification_passed}/{o.verification_total}
                                </span>
                              ) : null}
                              {o.duration_seconds !== null ? (
                                <span>{o.duration_seconds.toFixed(1)}s</span>
                              ) : null}
                            </div>
                            {o.summary ? (
                              <p className="text-muted-foreground mt-1">{o.summary}</p>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  <OperatorActionsPanel
                    actions={skillMetrics.data.operator_actions}
                    note="Copy-only safe commands. Browser does not execute skill metric operations."
                  />
                </>
              ) : null}
            </>
          )}
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
          {!diagnosticsEnabled ? (
            <p className="text-muted-foreground text-sm" data-testid="health-diagnostics-idle">
              Select an agent host in{" "}
              <span className="text-foreground font-medium">Hosts &amp; connections</span> to view
              live health data.
            </p>
          ) : (
            <>
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
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => health.refetch()}
                    >
                      Retry
                    </Button>
                  }
                />
              ) : null}

              {health.data ? (
                <>
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Status</p>
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
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Schema version</p>
                      <p className="mt-1 text-sm font-medium">v{health.data.schema_version}</p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Indexed sessions</p>
                      <p className="mt-1 text-sm font-medium">
                        {formatNumber(health.data.sessions)}
                      </p>
                    </div>
                    <div className="bg-card rounded-lg border p-3">
                      <p className="text-muted-foreground text-xs">Knowledge entries</p>
                      <p className="mt-1 text-sm font-medium">
                        {health.data.knowledge_entries !== undefined
                          ? formatNumber(health.data.knowledge_entries)
                          : "—"}
                      </p>
                    </div>
                  </div>

                  {health.data.last_indexed_at ? (
                    <p className="text-muted-foreground text-xs">
                      Last indexed:{" "}
                      <span className="text-foreground font-medium">
                        {health.data.last_indexed_at}
                      </span>
                    </p>
                  ) : null}
                </>
              ) : null}
            </>
          )}
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
                    <kbd className="bg-muted w-fit rounded border px-1.5 py-0.5 font-mono text-xs">
                      {item.keys}
                    </kbd>
                    <span className="text-muted-foreground">{item.action}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}

          <div className="text-muted-foreground flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs">
            <CheckCircle2 className="size-3.5 text-emerald-500" />
            Most shortcuts are disabled while typing in input fields.
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
