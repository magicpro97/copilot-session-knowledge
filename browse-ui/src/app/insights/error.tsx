"use client";

import { Banner } from "@/components/data/banner";
import { Button } from "@/components/ui/button";

type InsightsErrorProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function InsightsError({ error, reset }: InsightsErrorProps) {
  return (
    <Banner
      tone="danger"
      title="Insights failed to load"
      description={error.message || "Unexpected route error while rendering insights."}
      actions={
        <Button type="button" variant="outline" size="sm" onClick={reset}>
          Retry
        </Button>
      }
    />
  );
}
