export type CommandGroup = "navigate" | "actions" | "search" | "settings";

export type ThemePreference = "light" | "dark" | "system";
export type DensityPreference = "comfortable" | "compact";

export type PaletteCommand = {
  id: string;
  title: string;
  subtitle?: string;
  group: CommandGroup;
  keywords: string[];
  shortcut?: string;
  disabled?: boolean;
  run: () => void;
};

export type BuildPaletteCommandsOptions = {
  navigate: (href: string) => void;
  setTheme: (theme: ThemePreference) => void;
  setDensity: (density: DensityPreference) => void;
  recentSearches: string[];
};

export const RECENT_COMMANDS_KEY = "browse-ui-recent-commands";
export const RECENT_SEARCHES_KEY = "browse-ui-recent-searches";
const MAX_RECENT_ITEMS = 5;

const GROUP_ORDER: CommandGroup[] = ["navigate", "actions", "search", "settings"];

const COMMAND_KEYWORDS: Record<CommandGroup, string[]> = {
  navigate: ["go", "page", "route", "open"],
  actions: ["action", "workflow", "soon"],
  search: ["query", "find", "history", "recent"],
  settings: ["preferences", "theme", "density", "appearance"],
};

function normalize(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, " ");
}

function dedupeStrings(values: string[]): string[] {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function fuzzySubsequenceScore(query: string, text: string): number {
  if (!query) return 0;

  let queryIndex = 0;
  let score = 0;
  let consecutive = 0;

  for (let i = 0; i < text.length && queryIndex < query.length; i += 1) {
    if (text[i] === query[queryIndex]) {
      queryIndex += 1;
      consecutive += 1;
      score += 2 + consecutive;
    } else {
      consecutive = 0;
    }
  }

  return queryIndex === query.length ? score : -1;
}

function scoreText(query: string, text: string): number {
  const normalizedText = normalize(text);
  const normalizedQuery = normalize(query);

  if (!normalizedQuery) return 0;
  if (normalizedText === normalizedQuery) return 140;
  if (normalizedText.startsWith(normalizedQuery)) return 110;
  if (normalizedText.includes(normalizedQuery)) return 90;

  return fuzzySubsequenceScore(normalizedQuery, normalizedText);
}

export function readRecentSearches(storage: Storage | null): string[] {
  if (!storage) return [];

  const raw = storage.getItem(RECENT_SEARCHES_KEY);
  if (!raw) return [];

  try {
    const parsed = JSON.parse(raw) as unknown;

    if (!Array.isArray(parsed)) return [];

    const values = parsed
      .map((entry) => {
        if (typeof entry === "string") return entry;
        if (entry && typeof entry === "object") {
          const candidate = (entry as Record<string, unknown>).query;
          if (typeof candidate === "string") return candidate;
        }
        return "";
      })
      .map((value) => value.trim())
      .filter(Boolean)
      .slice(0, 10);

    return dedupeStrings(values);
  } catch {
    return [];
  }
}

export function readRecentCommandIds(storage: Storage | null): string[] {
  if (!storage) return [];

  const raw = storage.getItem(RECENT_COMMANDS_KEY);
  if (!raw) return [];

  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];

    return dedupeStrings(
      parsed.filter((entry): entry is string => typeof entry === "string")
    ).slice(0, MAX_RECENT_ITEMS);
  } catch {
    return [];
  }
}

export function writeRecentCommandIds(storage: Storage | null, ids: string[]): void {
  if (!storage) return;

  const sanitized = dedupeStrings(ids).slice(0, MAX_RECENT_ITEMS);
  storage.setItem(RECENT_COMMANDS_KEY, JSON.stringify(sanitized));
}

export function buildPaletteCommands({
  navigate,
  setTheme,
  setDensity,
  recentSearches,
}: BuildPaletteCommandsOptions): PaletteCommand[] {
  const commands: PaletteCommand[] = [
    {
      id: "nav:sessions",
      title: "Go to Sessions",
      subtitle: "Browse indexed sessions",
      group: "navigate",
      keywords: ["sessions", ...COMMAND_KEYWORDS.navigate],
      shortcut: "G S",
      run: () => navigate("/sessions"),
    },
    {
      id: "nav:search",
      title: "Go to Search",
      subtitle: "Find notes and patterns",
      group: "navigate",
      keywords: ["search", ...COMMAND_KEYWORDS.navigate],
      shortcut: "G /",
      run: () => navigate("/search"),
    },
    {
      id: "nav:insights",
      title: "Go to Insights",
      subtitle: "View analytics and trends",
      group: "navigate",
      keywords: ["insights", "analytics", ...COMMAND_KEYWORDS.navigate],
      shortcut: "G I",
      run: () => navigate("/insights"),
    },
    {
      id: "nav:graph",
      title: "Go to Graph",
      subtitle: "Explore relationships",
      group: "navigate",
      keywords: ["graph", "network", ...COMMAND_KEYWORDS.navigate],
      shortcut: "G G",
      run: () => navigate("/graph"),
    },
    {
      id: "nav:settings",
      title: "Go to Settings",
      subtitle: "Update preferences",
      group: "navigate",
      keywords: ["settings", "preferences", ...COMMAND_KEYWORDS.navigate],
      shortcut: "G ,",
      run: () => navigate("/settings"),
    },
    {
      id: "action:export",
      title: "Export Knowledge (coming soon)",
      subtitle: "Requires Pha 8 export workflow",
      group: "actions",
      keywords: ["export", "download", ...COMMAND_KEYWORDS.actions],
      disabled: true,
      run: () => undefined,
    },
    {
      id: "action:compare",
      title: "Compare Sessions (coming soon)",
      subtitle: "Requires comparison view",
      group: "actions",
      keywords: ["compare", "diff", ...COMMAND_KEYWORDS.actions],
      disabled: true,
      run: () => undefined,
    },
    {
      id: "theme:light",
      title: "Theme: Light",
      subtitle: "Switch to light mode",
      group: "settings",
      keywords: ["theme", "light", "appearance", ...COMMAND_KEYWORDS.settings],
      run: () => setTheme("light"),
    },
    {
      id: "theme:dark",
      title: "Theme: Dark",
      subtitle: "Switch to dark mode",
      group: "settings",
      keywords: ["theme", "dark", "appearance", ...COMMAND_KEYWORDS.settings],
      run: () => setTheme("dark"),
    },
    {
      id: "theme:system",
      title: "Theme: System",
      subtitle: "Follow system preference",
      group: "settings",
      keywords: ["theme", "system", "appearance", ...COMMAND_KEYWORDS.settings],
      run: () => setTheme("system"),
    },
    {
      id: "density:comfortable",
      title: "Density: Comfortable",
      subtitle: "More spacing for readability",
      group: "settings",
      keywords: ["density", "comfortable", "spacing", ...COMMAND_KEYWORDS.settings],
      run: () => setDensity("comfortable"),
    },
    {
      id: "density:compact",
      title: "Density: Compact",
      subtitle: "More information per screen",
      group: "settings",
      keywords: ["density", "compact", "spacing", ...COMMAND_KEYWORDS.settings],
      run: () => setDensity("compact"),
    },
  ];

  recentSearches.slice(0, 5).forEach((query, index) => {
    const encoded = encodeURIComponent(query);
    commands.push({
      id: `search:recent:${encoded}`,
      title: `Search: ${query}`,
      subtitle: "Recent query",
      group: "search",
      keywords: ["search", "recent", query, ...COMMAND_KEYWORDS.search],
      shortcut: index === 0 ? "↩" : undefined,
      run: () => navigate(`/search?q=${encoded}`),
    });
  });

  return commands;
}

export function buildRecentCommands(
  commands: PaletteCommand[],
  recentCommandIds: string[]
): PaletteCommand[] {
  const byId = new Map(commands.map((command) => [command.id, command]));
  return recentCommandIds
    .map((id) => byId.get(id))
    .filter((command): command is PaletteCommand => Boolean(command));
}

export function filterAndRankCommands(
  commands: PaletteCommand[],
  query: string,
  recentCommandIds: string[]
): PaletteCommand[] {
  const normalizedQuery = normalize(query);
  const recentRank = new Map(
    recentCommandIds.map((id, index) => [id, MAX_RECENT_ITEMS - index])
  );

  const scored = commands
    .map((command) => {
      const haystacks = [command.title, command.subtitle ?? "", ...command.keywords];
      const textScore = normalizedQuery
        ? Math.max(...haystacks.map((text) => scoreText(normalizedQuery, text)))
        : 0;

      if (normalizedQuery && textScore < 0) return null;

      const recencyBonus = (recentRank.get(command.id) ?? 0) * 3;
      const score = textScore + recencyBonus;

      if (normalizedQuery && score <= 0) return null;

      return {
        command,
        score,
      };
    })
    .filter((entry): entry is { command: PaletteCommand; score: number } => Boolean(entry));

  return scored
    .sort((left, right) => {
      if (right.score !== left.score) return right.score - left.score;
      if (left.command.group !== right.command.group) {
        return GROUP_ORDER.indexOf(left.command.group) - GROUP_ORDER.indexOf(right.command.group);
      }
      return left.command.title.localeCompare(right.command.title);
    })
    .map((entry) => entry.command);
}

export const COMMAND_GROUP_LABELS: Record<CommandGroup | "recent", string> = {
  recent: "Recent Commands",
  navigate: "Navigate",
  actions: "Actions",
  search: "Search History",
  settings: "Settings",
};

export const COMMAND_GROUP_ORDER: CommandGroup[] = GROUP_ORDER;
