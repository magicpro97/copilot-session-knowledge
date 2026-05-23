/**
 * Slash command registry for browse chat.
 *
 * Web-supported commands are intercepted and executed directly in the browser.
 * CLI-reference commands are listed for discoverability (visible in `/help`) but
 * are not intercepted — they must be entered in the Copilot CLI terminal.
 */

/** Where a slash command is available. */
export type CommandScope = "web" | "cli-reference";

export interface SlashCommand {
  /** Command name without the leading slash. */
  name: string;
  description: string;
  scope: CommandScope;
  /** Usage hint shown in the help surface. */
  usage: string;
}

/**
 * Authoritative slash command registry reflecting the current Copilot CLI surface.
 * Keep web-supported entries at the top for quick scanning.
 */
export const SLASH_COMMANDS: SlashCommand[] = [
  // ── Web-supported ────────────────────────────────────────────────────────────
  {
    name: "help",
    description: "Show available commands and the CLI command reference",
    scope: "web",
    usage: "/help",
  },
  {
    name: "skills",
    description: "Browse installed Copilot skills",
    scope: "web",
    usage: "/skills",
  },
  {
    name: "session",
    description: "Edit current session settings (name, model, mode)",
    scope: "web",
    usage: "/session",
  },
  {
    name: "new",
    description: "Create a new chat session",
    scope: "web",
    usage: "/new",
  },
  {
    name: "clear",
    description: "Clear the current draft and queued attachments",
    scope: "web",
    usage: "/clear",
  },
  {
    name: "mode",
    description: "Set the mode for the next run",
    scope: "web",
    usage: "/mode <interactive|plan|autopilot>",
  },
  {
    name: "history",
    description: "Open the CLI history picker to resume a Copilot CLI session",
    scope: "web",
    usage: "/history",
  },
  // ── CLI reference only ───────────────────────────────────────────────────────
  {
    name: "model",
    description: "Switch the model for the current session",
    scope: "cli-reference",
    usage: "/model <name>",
  },
  {
    name: "agent",
    description: "Switch to a specific agent",
    scope: "cli-reference",
    usage: "/agent <name>",
  },
  {
    name: "plan",
    description: "Enter plan mode",
    scope: "cli-reference",
    usage: "/plan",
  },
  {
    name: "research",
    description: "Start a research task",
    scope: "cli-reference",
    usage: "/research",
  },
  {
    name: "resume",
    description: "Resume the last session context",
    scope: "cli-reference",
    usage: "/resume",
  },
  {
    name: "rename",
    description: "Rename the current session",
    scope: "cli-reference",
    usage: "/rename <name>",
  },
  {
    name: "compact",
    description: "Compact the session context",
    scope: "cli-reference",
    usage: "/compact",
  },
  {
    name: "search",
    description: "Search session knowledge",
    scope: "cli-reference",
    usage: "/search <query>",
  },
  {
    name: "review",
    description: "Start a code review",
    scope: "cli-reference",
    usage: "/review",
  },
  {
    name: "tasks",
    description: "Show active tasks",
    scope: "cli-reference",
    usage: "/tasks",
  },
  {
    name: "mcp",
    description: "MCP server integration commands",
    scope: "cli-reference",
    usage: "/mcp",
  },
  {
    name: "plugin",
    description: "Manage plugins",
    scope: "cli-reference",
    usage: "/plugin",
  },
  {
    name: "delegate",
    description: "Delegate a sub-task to an agent",
    scope: "cli-reference",
    usage: "/delegate",
  },
  {
    name: "fleet",
    description: "Manage a fleet of agents",
    scope: "cli-reference",
    usage: "/fleet",
  },
  {
    name: "allow-all",
    description: "Allow all pending tool calls",
    scope: "cli-reference",
    usage: "/allow-all",
  },
  {
    name: "cwd",
    description: "Show or change working directory",
    scope: "cli-reference",
    usage: "/cwd [path]",
  },
];

const WEB_COMMAND_NAMES: ReadonlySet<string> = new Set(
  SLASH_COMMANDS.filter((c) => c.scope === "web").map((c) => c.name)
);

/**
 * Returns web-supported command suggestions as the user types a slash command prefix.
 *
 * Conservative safety rules:
 * - Returns `[]` for path-like inputs (e.g. `/Users/...`, `/home/...`, `/tmp/...`).
 * - Returns `[]` once the user has typed a space (arguments phase — no longer just a name).
 * - Returns `[]` for multi-line input.
 */
export function getCommandSuggestions(value: string): SlashCommand[] {
  // Only inspect the first line — multi-line values are never commands.
  const firstLineEnd = value.indexOf("\n");
  const firstLine = firstLineEnd === -1 ? value : value.slice(0, firstLineEnd);
  const trimmed = firstLine.trimStart();

  if (!trimmed.startsWith("/")) return [];

  const body = trimmed.slice(1); // strip leading slash

  // Path-like: contains another slash (e.g. /Users/foo)
  if (body.includes("/")) return [];

  // Arguments phase: user already typed a space — stop suggesting
  if (body.includes(" ")) return [];

  const prefix = body.toLowerCase();

  return SLASH_COMMANDS.filter((c) => c.scope === "web" && c.name.startsWith(prefix));
}

/**
 * Parses a submitted input as a web-supported slash command.
 *
 * Returns `null` for:
 * - Non-slash inputs
 * - Path-like inputs (`/Users/...`, `/home/...`, etc.) — these submit normally
 * - CLI-reference-only commands — also submit normally
 *
 * Only exact name matches (case-insensitive) against the web-supported set are accepted.
 */
export function parseWebCommand(value: string): { name: string; args: string } | null {
  const trimmed = value.trim();
  if (!trimmed.startsWith("/")) return null;

  const body = trimmed.slice(1);
  const spaceIdx = body.indexOf(" ");
  const commandName = (spaceIdx === -1 ? body : body.slice(0, spaceIdx)).toLowerCase();
  const args = spaceIdx === -1 ? "" : body.slice(spaceIdx + 1).trim();

  // Reject path-like: command name itself contains a slash
  if (commandName.includes("/")) return null;

  if (!WEB_COMMAND_NAMES.has(commandName)) return null;

  return { name: commandName, args };
}

/** Returns true if the given command name takes required arguments (e.g. `/mode`). */
export function commandHasArgs(name: string): boolean {
  return name === "mode";
}
