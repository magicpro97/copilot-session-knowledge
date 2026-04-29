"use client";

import { Button } from "@/components/ui/button";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="border-destructive/30 bg-destructive/10 rounded-xl border p-4">
      <p className="text-foreground text-sm font-medium">Graph page failed to load.</p>
      <p className="text-muted-foreground mt-1 text-sm">{error.message}</p>
      <Button type="button" variant="outline" size="sm" className="mt-3" onClick={reset}>
        Retry
      </Button>
    </div>
  );
}
