export const SEARCH_DEBOUNCE_MS = 300;
export const GLOBAL_CHORD_TIMEOUT_MS = 1_200;

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

export const SHORTCUT_GROUPS = [
  {
    title: "Global",
    items: [
      { keys: "⌘K / Ctrl+K", action: "Open or close the command palette" },
      { keys: "⌘B / Ctrl+B", action: "Toggle sidebar rail mode" },
      { keys: "Esc", action: "Close command palette" },
      { keys: "G then S", action: "Go to Sessions" },
      { keys: "G then /", action: "Go to Search" },
      { keys: "G then I", action: "Go to Insights" },
      { keys: "G then G", action: "Go to Graph" },
      { keys: "G then ,", action: "Go to Settings" },
      { keys: "?", action: "Open keyboard shortcuts in Settings" },
    ],
  },
  {
    title: "Sessions page",
    items: [
      { keys: "/", action: "Focus sessions search input" },
      { keys: "J / ↓", action: "Move focus to next row" },
      { keys: "K / ↑", action: "Move focus to previous row" },
      { keys: "Enter", action: "Open focused session" },
    ],
  },
  {
    title: "Search page",
    items: [
      { keys: "J / ↓", action: "Move active result down (when not typing)" },
      { keys: "K / ↑", action: "Move active result up (when not typing)" },
      { keys: "Enter", action: "Open active result (when not typing)" },
      { keys: "Esc", action: "Clear search input (while input is focused)" },
    ],
  },
  {
    title: "Session detail + tabs",
    items: [
      { keys: "1 / 2 / 3 / 4", action: "Switch session detail tabs" },
      { keys: "E", action: "Export current session markdown" },
      { keys: "C", action: "Open compare sheet" },
      { keys: "Space", action: "Play/pause timeline replay (Timeline tab)" },
      { keys: "← / →", action: "Step timeline event (Timeline tab)" },
    ],
  },
  {
    title: "Insights page",
    items: [
      { keys: "1", action: "Show Dashboard tab" },
      { keys: "2", action: "Show Live feed tab" },
    ],
  },
  {
    title: "Graph",
    items: [
      { keys: "F", action: "Fit relationships graph to viewport" },
      { keys: "R", action: "Reset graph filters or selected category" },
    ],
  },
] as const;
