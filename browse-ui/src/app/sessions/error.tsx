"use client";

import { Banner } from "@/components/data/banner";
import { Button } from "@/components/ui/button";

type SessionsErrorProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function SessionsError({ error, reset }: SessionsErrorProps) {
  return (
    <div className="space-y-3">
      <h1 className="text-2xl font-semibold tracking-tight">Sessions</h1>
      <Banner
        tone="danger"
        title={error.message || "We hit an unexpected error while rendering sessions"}
        description="Please retry. If this keeps happening, reload the page and verify the API server is available."
        actions={
          <Button type="button" variant="outline" size="sm" onClick={reset}>
            Retry
          </Button>
        }
      />
    </div>
  );
}
