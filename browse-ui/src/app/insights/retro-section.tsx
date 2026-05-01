"use client";

import { Banner } from "@/components/data/banner";
import { InsightActionList } from "@/components/data/insight-action-list";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useRetro } from "@/lib/api/hooks";
import { deriveActionsFromStrings } from "@/lib/insight-derive";
import { formatNumber } from "@/lib/formatters";
import type { RetroScout } from "@/lib/api/types";

const SECTION_LABELS: Record<string, string> = {
  knowledge: "Knowledge",
  skills: "Skills",
  hooks: "Hooks",
  git: "Git",
};

const DISTORTION_EXPLANATIONS: Record<string, string> = {
  hook_deny_dry_noise:
    "Dry-run/test deny-dry entries were excluded from deny_rate — these are not real enforcement denials.",
  skills_unverified:
    "Skill outcomes exist but verification evidence is missing — confidence is lower until outcomes are verified.",
};

function scoreBadgeVariant(score: number): "outline" | "secondary" | "destructive" {
  if (score >= 80) return "outline";
  if (score >= 50) return "secondary";
  return "destructive";
}

function confidenceBadgeVariant(
  confidence: "low" | "medium" | "high"
): "outline" | "secondary" | "destructive" {
  if (confidence === "high") return "outline";
  if (confidence === "medium") return "secondary";
  return "destructive";
}

function ScoutCoveragePanel({ scout }: { scout: RetroScout }) {
  if (!scout.available) {
    return (
      <div className="rounded-lg border px-3 py-2">
        <p className="text-muted-foreground text-xs font-medium">🔭 Trend Scout</p>
        <p className="text-muted-foreground mt-1 text-xs">Not configured</p>
      </div>
    );
  }

  const graceActive = scout.would_skip_without_force;
  const graceLabel =
    graceActive && scout.remaining_hours != null
      ? `active (${scout.remaining_hours.toFixed(1)}h remaining)`
      : scout.grace_window_hours
        ? "inactive — eligible to run"
        : "disabled";

  const lastRunLabel = scout.last_run_utc
    ? `${scout.last_run_utc}${scout.elapsed_hours != null ? ` (${scout.elapsed_hours.toFixed(1)}h ago)` : ""}`
    : "(never / state file missing)";

  return (
    <div className="rounded-lg border px-3 py-2">
      <p className="text-xs font-medium">🔭 Trend Scout</p>
      <ul className="text-muted-foreground mt-1 space-y-0.5 text-xs">
        <li>
          Repo:{" "}
          <span className="text-foreground font-medium">{scout.target_repo ?? "(unset)"}</span>
        </li>
        <li>
          Label: <code className="text-xs">{scout.issue_label ?? "(unset)"}</code>
        </li>
        <li>
          Grace window: {scout.grace_window_hours}h ·{" "}
          <span className={graceActive ? "text-yellow-500" : "text-green-500"}>{graceLabel}</span>
        </li>
        <li>Last run: {lastRunLabel}</li>
      </ul>
    </div>
  );
}

/** Inner content shared by RetroSection (collapsible) and RetroTab (full view). */
export function RetroBody({ retro }: { retro: ReturnType<typeof useRetro> }) {
  if (retro.isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-6 w-36" />
        <Skeleton className="h-6 w-40" />
      </div>
    );
  }
  if (retro.isError) {
    return (
      <Banner
        tone="warning"
        title="Retrospective unavailable"
        description={
          retro.error instanceof Error ? retro.error.message : "Could not load /api/retro/summary."
        }
      />
    );
  }
  if (!retro.data) return null;
  return (
    <div className="space-y-4">
      {retro.data.summary ? (
        <p className="text-muted-foreground text-sm">{retro.data.summary}</p>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {retro.data.available_sections.map((section) => {
          const score = retro.data!.subscores[section];
          return (
            <div key={section} className="rounded-lg border px-3 py-2">
              <p className="text-muted-foreground text-xs">{SECTION_LABELS[section] ?? section}</p>
              <p className="mt-1 text-lg font-semibold">
                {score != null ? formatNumber(score) : "–"}
              </p>
            </div>
          );
        })}
      </div>

      {retro.data.distortion_flags && retro.data.distortion_flags.length > 0 ? (
        <div className="rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-3 py-2">
          <p className="text-xs font-medium text-yellow-600 dark:text-yellow-400">
            ⚠️ Score distortions
          </p>
          <ul className="mt-1 space-y-1">
            {retro.data.distortion_flags.map((flag) => (
              <li key={flag} className="text-xs">
                <span className="font-mono font-semibold">{flag}</span>
                {DISTORTION_EXPLANATIONS[flag] ? (
                  <span className="text-muted-foreground ml-1">
                    — {DISTORTION_EXPLANATIONS[flag]}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {retro.data.accuracy_notes && retro.data.accuracy_notes.length > 0 ? (
        <div>
          <p className="text-muted-foreground text-xs font-medium">Accuracy notes</p>
          <ul className="text-muted-foreground mt-1 list-disc space-y-0.5 pl-4">
            {retro.data.accuracy_notes.map((note, i) => (
              <li key={i} className="text-xs">
                {note}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {retro.data.improvement_actions && retro.data.improvement_actions.length > 0 ? (
        <InsightActionList
          actions={deriveActionsFromStrings(retro.data.improvement_actions)}
          title="Recommended actions"
        />
      ) : null}

      {retro.data.scout ? <ScoutCoveragePanel scout={retro.data.scout} /> : null}

      <p className="text-muted-foreground text-xs">
        mode: {retro.data.mode} · generated {retro.data.generated_at}
      </p>
    </div>
  );
}

export function RetroSection() {
  const retro = useRetro("repo");

  if (retro.isSuccess && !retro.data) {
    return null;
  }

  return (
    <details className="bg-card rounded-xl border p-4">
      <summary className="cursor-pointer list-none text-sm font-medium">
        <span className="inline-flex items-center gap-2">
          Retrospective
          {retro.isSuccess && retro.data ? (
            <Badge variant={scoreBadgeVariant(retro.data.retro_score)}>
              {retro.data.grade_emoji} {retro.data.grade} ({formatNumber(retro.data.retro_score)})
            </Badge>
          ) : null}
          {retro.isSuccess && retro.data?.score_confidence ? (
            <Badge variant={confidenceBadgeVariant(retro.data.score_confidence)}>
              confidence: {retro.data.score_confidence}
            </Badge>
          ) : null}
        </span>
      </summary>

      <div className="mt-4">
        <RetroBody retro={retro} />
      </div>
    </details>
  );
}
