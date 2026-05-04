"use client";

import { CheckCircle, Loader2, RefreshCcw } from "lucide-react";

import { InsightFindingCard } from "@/components/data/insight-finding-card";
import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkflowHealth } from "@/lib/api/hooks";
import type { WorkflowFinding } from "@/lib/api/types";
import type { InsightFinding } from "@/lib/insight-models";
import { useInsightsTab } from "./insights-tab-context";
import { HostedIdleGuidance } from "./overview-tab";

function gradeBadgeVariant(grade: string): "outline" | "secondary" | "destructive" {
  if (grade === "A" || grade === "B") return "outline";
  if (grade === "C") return "secondary";
  return "destructive";
}

function toInsightFinding(f: WorkflowFinding): InsightFinding {
  return {
    id: f.id,
    title: f.title,
    detail: f.detail,
    severity: f.severity,
    why: f.action || undefined,
  };
}

function WorkflowTabContent() {
  const { host, diagnosticsEnabled } = useInsightsTab();
  const workflow = useWorkflowHealth(host, diagnosticsEnabled);
  const isReloading = workflow.isFetching && !workflow.isLoading;

  function handleReload() {
    if (isReloading) return;
    void workflow.refetch();
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium">Workflow Health</h2>
        {workflow.isSuccess && workflow.data ? (
          <Badge variant={gradeBadgeVariant(workflow.data.health_grade)}>
            Grade: {workflow.data.health_grade}
          </Badge>
        ) : null}
      </div>

      {workflow.isLoading ? (
        <div className="space-y-2" data-testid="workflow-loading">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : workflow.isError ? (
        <Banner
          tone="danger"
          title="Workflow health unavailable"
          description="Could not load workflow health data. Run workflow-health.py to generate a report."
          actions={
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={isReloading}
              onClick={handleReload}
            >
              {isReloading ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <RefreshCcw className="size-3.5" />
              )}
              {isReloading ? "Reloading…" : "Reload"}
            </Button>
          }
        />
      ) : workflow.data?.findings.length === 0 ? (
        <EmptyState
          title="No workflow findings"
          description={
            "All checks passed, or no data available yet. Run workflow-health.py to generate findings."
          }
          icon={<CheckCircle className="size-5" />}
        />
      ) : workflow.data ? (
        <div role="list" className="space-y-2">
          {workflow.data.findings.map((finding) => (
            <InsightFindingCard key={finding.id} finding={toInsightFinding(finding)} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function WorkflowTab() {
  const { diagnosticsEnabled } = useInsightsTab();
  if (!diagnosticsEnabled) {
    return <HostedIdleGuidance />;
  }
  return <WorkflowTabContent />;
}
