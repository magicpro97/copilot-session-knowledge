"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
  CommandSeparator,
} from "@/components/ui/command";
import { useDensity } from "@/hooks/use-density";
import {
  buildPaletteCommands,
  buildRecentCommands,
  COMMAND_GROUP_LABELS,
  COMMAND_GROUP_ORDER,
  filterAndRankCommands,
  readRecentCommandIds,
  readRecentSearches,
  writeRecentCommandIds,
  type PaletteCommand,
} from "@/lib/commands";

export function CommandPalette() {
  const router = useRouter();
  const { setTheme } = useTheme();
  const [, setDensity] = useDensity();

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [recentSearches, setRecentSearches] = useState<string[]>([]);
  const [recentCommandIds, setRecentCommandIds] = useState<string[]>([]);

  useEffect(() => {
    if (!open) return;
    setRecentSearches(readRecentSearches(window.localStorage));
    setRecentCommandIds(readRecentCommandIds(window.localStorage));
  }, [open]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase();
      if ((event.metaKey || event.ctrlKey) && key === "k") {
        event.preventDefault();
        setOpen((isOpen) => !isOpen);
        return;
      }

      if (key === "escape") {
        setOpen(false);
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const commands = useMemo(
    () =>
      buildPaletteCommands({
        navigate: (href) => router.push(href),
        setTheme,
        setDensity,
        recentSearches,
      }),
    [router, setTheme, setDensity, recentSearches]
  );

  const recentCommands = useMemo(
    () => buildRecentCommands(commands, recentCommandIds),
    [commands, recentCommandIds]
  );

  const filteredCommands = useMemo(
    () => filterAndRankCommands(commands, query, recentCommandIds),
    [commands, query, recentCommandIds]
  );

  const visibleCommands = useMemo(() => {
    if (!query.trim()) {
      const recentIds = new Set(recentCommands.map((command) => command.id));
      return commands.filter((command) => !recentIds.has(command.id));
    }

    return filteredCommands;
  }, [commands, filteredCommands, query, recentCommands]);

  const groupedCommands = useMemo(() => {
    return COMMAND_GROUP_ORDER.map((group) => ({
      group,
      commands: visibleCommands.filter((command) => command.group === group),
    })).filter(({ commands: grouped }) => grouped.length > 0);
  }, [visibleCommands]);

  const totalVisible = groupedCommands.reduce(
    (count, section) => count + section.commands.length,
    0
  );

  const runCommand = (command: PaletteCommand) => {
    if (command.disabled) return;

    command.run();

    const nextRecent = [command.id, ...recentCommandIds.filter((id) => id !== command.id)].slice(0, 5);
    setRecentCommandIds(nextRecent);
    writeRecentCommandIds(window.localStorage, nextRecent);

    setOpen(false);
    setQuery("");
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (!nextOpen) setQuery("");
      }}
      showCloseButton={false}
    >
      <Command shouldFilter={false}>
        <CommandInput
          value={query}
          onValueChange={setQuery}
          placeholder="Type a command or search history..."
        />
        <CommandList>
          {totalVisible === 0 && (!recentCommands.length || Boolean(query.trim())) ? (
            <CommandEmpty>No matching commands.</CommandEmpty>
          ) : null}

          {!query.trim() && recentCommands.length > 0 ? (
            <>
              <CommandGroup heading={COMMAND_GROUP_LABELS.recent}>
                {recentCommands.map((command) => (
                  <CommandItem
                    key={`recent-${command.id}`}
                    value={`${command.title} ${command.keywords.join(" ")}`}
                    onSelect={() => runCommand(command)}
                    disabled={command.disabled}
                  >
                    <span>{command.title}</span>
                    {command.shortcut ? <CommandShortcut>{command.shortcut}</CommandShortcut> : null}
                  </CommandItem>
                ))}
              </CommandGroup>
              <CommandSeparator />
            </>
          ) : null}

          {groupedCommands.map(({ group, commands: commandsInGroup }, index) => (
            <div key={group}>
              <CommandGroup heading={COMMAND_GROUP_LABELS[group]}>
                {commandsInGroup.map((command) => (
                  <CommandItem
                    key={command.id}
                    value={`${command.title} ${command.keywords.join(" ")}`}
                    onSelect={() => runCommand(command)}
                    disabled={command.disabled}
                  >
                    <div className="flex min-w-0 flex-col">
                      <span>{command.title}</span>
                      {command.subtitle ? (
                        <span className="truncate text-xs text-muted-foreground">
                          {command.subtitle}
                        </span>
                      ) : null}
                    </div>
                    {command.shortcut ? <CommandShortcut>{command.shortcut}</CommandShortcut> : null}
                  </CommandItem>
                ))}
              </CommandGroup>
              {index < groupedCommands.length - 1 ? <CommandSeparator /> : null}
            </div>
          ))}
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
