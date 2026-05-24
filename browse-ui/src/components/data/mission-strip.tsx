/**
 * Flight Recorder v3 — Mission strip (synthesis §4d/§4e).
 *
 * A single row mounted between the existing DebugLogFlowChart header
 * and the TraceOverviewStrip. Consumes only the safe `MissionRollup`
 * output of `deriveMissionRollup` — never raw entries / `attrs` /
 * messages. Compactions render as a count only (synthesis §4f).
 *
 * Selectors:
 *   - data-testid="flight-recorder-mission-strip"
 *   - data-testid="mission-chip-tools" | "mission-chip-hooks" | "mission-chip-skills"
 *     | "mission-chip-subagents" | "mission-chip-compactions" | "mission-chip-errors"
 *   - data-testid="mission-chip-tool-<slug>"
 *   - data-testid="mission-chip-hook-<slug>"
 */
import type { MissionRollup } from "@/lib/flight-recorder";

export type MissionStripProps = {
  rollup: MissionRollup;
  /** Called when an aggregate chip is clicked (category-level navigation). */
  onSelectCategory?: (
    category: "tool" | "hook" | "skill" | "subagent" | "compaction" | "error"
  ) => void;
  /** Called when a specific tool/hook item chip is clicked. */
  onSelectItem?: (kind: "tool" | "hook", name: string) => void;
  /** Max top-N item chips to render per category. Defaults to 5. */
  topN?: number;
};

const SAFE_SLUG_RE = /[^a-z0-9]+/g;

export function safeSlug(input: string): string {
  // Stable, host-safe slug for a `data-testid`. Lowercase ASCII, no control
  // chars, dashes for separators, clamped to 48 chars. Empty input ⇒ "none".
  if (typeof input !== "string" || input.length === 0) return "none";
  const cleaned = input
    .replace(/[\u0000-\u001f\u007f]/g, "")
    .trim()
    .toLowerCase();
  const slugged = cleaned.replace(SAFE_SLUG_RE, "-").replace(/(^-+|-+$)/g, "");
  if (slugged.length === 0) return "none";
  return slugged.length > 48 ? slugged.slice(0, 48) : slugged;
}

type ChipProps = {
  testid: string;
  label: string;
  count: number;
  onClick?: () => void;
};

function Chip({ testid, label, count, onClick }: ChipProps) {
  const content = `${label}: ${count.toLocaleString()}`;
  return (
    <button
      type="button"
      data-testid={testid}
      aria-label={content}
      className="border-border bg-muted/30 hover:bg-muted inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs"
      onClick={onClick}
    >
      <span>{content}</span>
    </button>
  );
}

export function MissionStrip({
  rollup,
  onSelectCategory,
  onSelectItem,
  topN = 5,
}: MissionStripProps) {
  const topTools = rollup.tools.slice(0, topN);
  const topHooks = rollup.hooks.slice(0, topN);

  return (
    <div
      role="region"
      aria-label="Mission rollup"
      data-testid="flight-recorder-mission-strip"
      className="border-border bg-card/60 flex flex-wrap items-center gap-2 border-b px-3 py-1.5"
    >
      <Chip
        testid="mission-chip-tools"
        label="Tools used"
        count={rollup.tools.reduce((a, b) => a + b.count, 0)}
        onClick={() => onSelectCategory?.("tool")}
      />
      {topTools.map((chip) => (
        <button
          key={`tool-${chip.name}`}
          type="button"
          data-testid={`mission-chip-tool-${safeSlug(chip.name)}`}
          aria-label={`${chip.name}: ${chip.count}`}
          onClick={() => onSelectItem?.("tool", chip.name)}
          className="border-border/50 bg-muted/10 hover:bg-muted/40 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px]"
          title={`${chip.name} (${chip.count})`}
        >
          <span className="font-mono">{chip.name}</span>
          <span className="text-muted-foreground">·</span>
          <span>{chip.count}</span>
        </button>
      ))}

      <Chip
        testid="mission-chip-hooks"
        label="Hooks fired"
        count={rollup.hooks.reduce((a, b) => a + b.count, 0)}
        onClick={() => onSelectCategory?.("hook")}
      />
      {topHooks.map((chip) => (
        <button
          key={`hook-${chip.name}`}
          type="button"
          data-testid={`mission-chip-hook-${safeSlug(chip.name)}`}
          aria-label={`${chip.name}: ${chip.count}`}
          onClick={() => onSelectItem?.("hook", chip.name)}
          className="border-border/50 bg-muted/10 hover:bg-muted/40 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px]"
          title={`${chip.name} (${chip.count})`}
        >
          <span className="font-mono">{chip.name}</span>
          <span className="text-muted-foreground">·</span>
          <span>{chip.count}</span>
        </button>
      ))}

      <Chip
        testid="mission-chip-skills"
        label="Skills"
        count={rollup.skills.reduce((a, b) => a + b.count, 0)}
        onClick={() => onSelectCategory?.("skill")}
      />
      <Chip
        testid="mission-chip-subagents"
        label="Sub-agents"
        count={rollup.subagents.reduce((a, b) => a + b.count, 0)}
        onClick={() => onSelectCategory?.("subagent")}
      />
      <Chip
        testid="mission-chip-compactions"
        label="Compactions"
        count={rollup.compactions}
        onClick={() => onSelectCategory?.("compaction")}
      />
      <Chip
        testid="mission-chip-errors"
        label="Errors"
        count={rollup.errorCount}
        onClick={() => onSelectCategory?.("error")}
      />
    </div>
  );
}
