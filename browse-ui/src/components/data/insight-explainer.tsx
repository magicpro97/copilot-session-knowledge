import { cn } from "@/lib/utils";

type InsightExplainerProps = {
  /** Summary or explanation text to display. */
  text: string;
  /** Optional ISO timestamp string shown as a generation footer. */
  generatedAt?: string;
  className?: string;
};

/**
 * Presentational explainer block for insight summary text and optional
 * generation timestamp. Fetch-free; driven by props only.
 */
export function InsightExplainer({ text, generatedAt, className }: InsightExplainerProps) {
  return (
    <div className={cn("space-y-1", className)}>
      <p className="text-muted-foreground text-sm">{text}</p>
      {generatedAt ? (
        <p className="text-muted-foreground text-xs">generated {generatedAt}</p>
      ) : null}
    </div>
  );
}
