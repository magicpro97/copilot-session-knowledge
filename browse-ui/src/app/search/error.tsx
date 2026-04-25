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
    <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4">
      <p className="text-sm font-medium text-foreground">Search page failed to load.</p>
      <p className="mt-1 text-sm text-muted-foreground">{error.message}</p>
      <Button type="button" variant="outline" size="sm" className="mt-3" onClick={reset}>
        Retry
      </Button>
    </div>
  );
}
