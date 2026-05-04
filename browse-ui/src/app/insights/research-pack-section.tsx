"use client";

import { Loader2, RefreshCcw } from "lucide-react";

import { Banner } from "@/components/data/banner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useReloadScoutResearchPack, useScoutResearchPack } from "@/lib/api/hooks";
import type { ResearchPackRepo } from "@/lib/api/types";
import { useInsightsTab } from "./insights-tab-context";

function RepoCard({ repo }: { repo: ResearchPackRepo }) {
  const hasNoveltySig = repo.novelty_signals.length > 0;
  const hasRiskSig = repo.risk_signals.length > 0;
  const hasFollowups = repo.recommended_followups.length > 0;

  return (
    <div className="space-y-1 rounded-lg border px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <a
          href={repo.html_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-foreground text-sm font-semibold hover:underline"
        >
          {repo.full_name}
        </a>
        {repo.language ? (
          <Badge variant="secondary" className="text-xs">
            {repo.language}
          </Badge>
        ) : null}
        <span className="text-muted-foreground text-xs">
          ⭐ {repo.stars} · score {repo.score.toFixed(2)}
        </span>
      </div>
      {repo.discovery_lane ? (
        <p className="text-muted-foreground text-xs">
          Lane: <code className="text-xs">{repo.discovery_lane}</code>
        </p>
      ) : null}
      {hasNoveltySig ? (
        <ul className="list-disc space-y-0.5 pl-4 text-xs text-green-600 dark:text-green-400">
          {repo.novelty_signals.slice(0, 3).map((sig, i) => (
            <li key={i}>{sig}</li>
          ))}
        </ul>
      ) : null}
      {hasRiskSig ? (
        <ul className="list-disc space-y-0.5 pl-4 text-xs text-yellow-600 dark:text-yellow-400">
          {repo.risk_signals.slice(0, 2).map((sig, i) => (
            <li key={i}>{sig}</li>
          ))}
        </ul>
      ) : null}
      {hasFollowups ? (
        <p className="text-muted-foreground text-xs">Follow-up: {repo.recommended_followups[0]}</p>
      ) : null}
    </div>
  );
}

function ResearchPackSectionContent() {
  const { host, diagnosticsEnabled } = useInsightsTab();
  const pack = useScoutResearchPack(host, diagnosticsEnabled);
  const reloadPack = useReloadScoutResearchPack(host);
  const reloadFailureDescription =
    reloadPack.error instanceof Error
      ? reloadPack.error.message
      : "Trend Scout reload request failed before a refreshed research-pack response was returned.";

  function handleReload() {
    if (reloadPack.isPending) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        "Run python3 trend-scout.py --research-pack now?\n\nThis may create or update Trend Scout issues if your current Trend Scout config is live."
      )
    ) {
      return;
    }
    reloadPack.mutate();
  }

  return (
    <details className="bg-card rounded-xl border p-4">
      <summary className="cursor-pointer list-none text-sm font-medium">
        <span className="inline-flex items-center gap-2">
          Trend Scout Research Pack
          {pack.isSuccess && pack.data ? (
            pack.data.available ? (
              <>
                <Badge variant="outline">{pack.data.repo_count} repos</Badge>
                {pack.data.run_skipped ? <Badge variant="secondary">skipped</Badge> : null}
              </>
            ) : (
              <Badge variant="secondary">unavailable</Badge>
            )
          ) : null}
        </span>
      </summary>

      <div className="mt-4 space-y-4">
        <div className="space-y-2 rounded-lg border border-dashed px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={reloadPack.isPending}
              onClick={handleReload}
            >
              {reloadPack.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <RefreshCcw className="size-3.5" />
              )}
              {reloadPack.isPending ? "Running Trend Scout…" : "Reload pack"}
            </Button>
            <p className="text-muted-foreground text-xs">
              Runs <code>python3 trend-scout.py --research-pack</code> locally.
            </p>
          </div>
          <p className="text-muted-foreground text-xs">
            This is a live Trend Scout action and may create or update Trend Scout issues if the
            current config is active.
          </p>
        </div>

        {reloadPack.isError ? (
          <Banner
            tone="warning"
            title="Research pack reload failed"
            description={reloadFailureDescription}
          />
        ) : reloadPack.data ? (
          reloadPack.data.ok ? (
            <Banner
              tone="info"
              title="Research pack reloaded"
              description={
                reloadPack.data.run_skipped
                  ? (reloadPack.data.skip_reason ??
                    "Trend Scout wrote a skipped research-pack artifact because the grace window is active.")
                  : `Trend Scout refreshed the research pack with ${reloadPack.data.repo_count} repo(s).`
              }
            />
          ) : (
            <Banner
              tone="warning"
              title="Research pack reload failed"
              description={
                reloadPack.data.error ??
                "Trend Scout did not produce a refreshed research pack artifact."
              }
            />
          )
        ) : null}

        {pack.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-6 w-64" />
            <Skeleton className="h-6 w-48" />
          </div>
        ) : pack.isError ? (
          <Banner
            tone="warning"
            title="Research pack unavailable"
            description={
              pack.error instanceof Error
                ? pack.error.message
                : "Could not load /api/scout/research-pack."
            }
          />
        ) : pack.data ? (
          pack.data.available ? (
            <>
              {pack.data.run_skipped ? (
                <Banner
                  tone="info"
                  title="Run was skipped (grace window active)"
                  description={pack.data.skip_reason ?? "No repos collected this cycle."}
                />
              ) : null}

              {pack.data.generated_at ? (
                <p className="text-muted-foreground text-xs">Generated: {pack.data.generated_at}</p>
              ) : null}

              {pack.data.repos.length > 0 ? (
                <div className="space-y-2">
                  {pack.data.repos.map((repo) => (
                    <RepoCard key={repo.full_name} repo={repo} />
                  ))}
                </div>
              ) : (
                <p className="text-muted-foreground text-sm">No repos in this pack.</p>
              )}
            </>
          ) : (
            <p className="text-muted-foreground text-sm">
              {pack.data.error
                ? `Pack unavailable: ${pack.data.error}`
                : "No research pack found. Run: python3 trend-scout.py --research-pack"}
            </p>
          )
        ) : null}
      </div>
    </details>
  );
}

export function ResearchPackSection() {
  const { diagnosticsEnabled } = useInsightsTab();
  if (!diagnosticsEnabled) return null;
  return <ResearchPackSectionContent />;
}
