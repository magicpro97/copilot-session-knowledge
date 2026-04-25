import { cn } from "@/lib/utils";

type HighlightPart = {
  text: string;
  highlighted: boolean;
};

export function splitHighlightParts(text: string, query: string): HighlightPart[] {
  if (!query.trim() || !text) return [{ text, highlighted: false }];

  const rawTokens = Array.from(
    new Set(
      query
        .trim()
        .split(/\s+/)
        .map((item) => item.trim())
        .filter(Boolean)
    )
  ).filter(Boolean);

  const tokenSet = new Set(rawTokens.map((token) => token.toLowerCase()));
  const tokens = rawTokens.map((token) =>
    token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  );

  if (!tokens.length) return [{ text, highlighted: false }];

  const expression = new RegExp(`(${tokens.join("|")})`, "gi");
  const parts = text.split(expression);
  return parts
    .filter((part) => part.length > 0)
    .map((part) => ({
      text: part,
      highlighted: tokenSet.has(part.toLowerCase()),
    }));
}

type HighlightProps = {
  text: string;
  query?: string;
  className?: string;
};

export function Highlight({ text, query = "", className }: HighlightProps) {
  const parts = splitHighlightParts(text, query);
  return (
    <span className={cn("break-words", className)}>
      {parts.map((part, index) =>
        part.highlighted ? (
          <mark
            key={`${part.text}-${index}`}
            className="rounded bg-primary/15 px-0.5 text-foreground"
          >
            {part.text}
          </mark>
        ) : (
          <span key={`${part.text}-${index}`}>{part.text}</span>
        )
      )}
    </span>
  );
}
