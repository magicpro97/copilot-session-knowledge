export const SEARCH_DEBOUNCE_MS = 300;

export const PAGE_SIZES = [20, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = PAGE_SIZES[0];

export const STALE_TIMES = {
  sessions: 30_000,
  sessionDetail: 30_000,
  search: 10_000,
  health: 10_000,
  dashboard: 60_000,
  graph: 60_000,
  embeddings: 60_000,
  eval: 60_000,
  compare: 15_000,
} as const;

export const CACHE_TIMES = {
  sessions: 5 * 60_000,
  sessionDetail: 5 * 60_000,
  search: 2 * 60_000,
  health: 60_000,
  dashboard: 10 * 60_000,
  graph: 10 * 60_000,
  embeddings: 10 * 60_000,
  eval: 10 * 60_000,
  compare: 60_000,
} as const;

export const SOURCE_LABELS = {
  copilot: "Copilot",
  claude: "Claude",
  gemini: "Gemini",
  codex: "Codex",
  chatgpt: "ChatGPT",
} as const;

export const SOURCE_BADGE_CLASSNAMES: Record<string, string> = {
  copilot:
    "border-transparent bg-[hsl(201_100%_93%)] text-[hsl(211_94%_35%)] dark:bg-[hsl(212_60%_10%)] dark:text-[hsl(213_92%_67%)]",
  claude:
    "border-transparent bg-[hsl(30_100%_94%)] text-[hsl(18_66%_37%)] dark:bg-[hsl(30_100%_8%)] dark:text-[hsl(29_86%_59%)]",
  gemini:
    "border-transparent bg-[hsl(142_72%_91%)] text-[hsl(145_77%_24%)] dark:bg-[hsl(154_64%_8%)] dark:text-[hsl(158_64%_52%)]",
  codex:
    "border-transparent bg-[hsl(258_100%_96%)] text-[hsl(265_66%_45%)] dark:bg-[hsl(267_69%_13%)] dark:text-[hsl(264_67%_76%)]",
  chatgpt:
    "border-transparent bg-[hsl(258_100%_96%)] text-[hsl(265_66%_45%)] dark:bg-[hsl(267_69%_13%)] dark:text-[hsl(264_67%_76%)]",
};
